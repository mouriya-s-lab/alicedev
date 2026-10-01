"""Steward user/tool flows over the real Store, scheduler, intake and outbox."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path

from rt_support import ADMIN, CHAT, USER, Env, inbound, make_env

from alicedev.agent_tools.model import ToolError, ToolFailure, ToolName, ToolRequest
from alicedev.agent_tools.parser import parse_tool_request
from alicedev.agent_tools.results import (
    Chats, LinkReceipt, SessionDetails, StartReceipt, ToolDiscovery, ToolResult, ToolSuccess,
)
from alicedev.domain import JsonValue, SessionView
from alicedev.dsl.model import AgentState
from alicedev.scheduler.engine import StartOutcome
from alicedev.status import BotStatus
from alicedev.store.outbox_repo import OutboxRepo, OutboxState
from alicedev.store.sessions_repo import SessionRow

OTHER_CHAT = "telegram:GroupMessage:-200"


def request(ref: str, call_id: str, name: ToolName,
            args: dict[str, JsonValue]) -> ToolRequest:
    parsed = parse_tool_request(name.value, {"agent": ref, "call_id": call_id, "args": args})
    assert isinstance(parsed, ToolRequest), parsed
    return parsed


async def steward(env: Env, *, chat: str = CHAT) -> tuple[SessionRow, str]:
    assert await env.dispatcher.handle(inbound("/管家 分诊", user=ADMIN, chat=chat))
    await env.drain()
    row = await env.sessions.current(chat)
    assert row is not None and row.scenario == "steward"
    agent = await env.agents.for_state(row.session_id, row.state)
    assert agent is not None and agent.agent_id is not None
    return row, agent.agent_ref


def test_steward_admin_only_one_per_chat_and_reuses_existing_conversation(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            assert await env.dispatcher.handle(inbound("/管家 不应接收", user=USER))
            assert await env.texts() == []
            assert await env.sessions.current(CHAT) is None
            assert env.paseo.created == []
            row, _ = await steward(env)
            assert row.created_by == ADMIN
            first_agent = env.paseo.created[0]
            assert await env.dispatcher.handle(inbound("/管家 再说", user=ADMIN))
            await env.drain()
            assert await env.sessions.by_no(CHAT, 2) is None
            assert len(env.paseo.created) == 1
            assert (await env.sessions.current(CHAT)).session_id == row.session_id
            assert len(env.paseo.sent) == 1
            agent_id, command = env.paseo.sent[0]
            assert agent_id == first_agent["agent_id"]
            assert command.startswith("/chat_ingress ")
            assert json.loads(command[len("/chat_ingress "):])["text"] == "再说"
            other, _ = await steward(env, chat=OTHER_CHAT)
            assert other.session_id != row.session_id and other.created_by == ADMIN
            assert (await env.sessions.current(CHAT)).session_id == row.session_id
            assert (await env.sessions.current(OTHER_CHAT)).session_id == other.session_id
        finally:
            await env.close()

    asyncio.run(main())


def test_steward_rejects_non_admin_chat_input_silently(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            row, _ = await steward(env)
            agent_id = env.paseo.created[0]["agent_id"]
            assert await env.dispatcher.handle(inbound("问管家", user=USER, addressed=True))
            assert await env.dispatcher.handle(inbound(f"/继续 %{row.no} 问管家", user=USER))
            assert await env.texts() == []
            assert env.paseo.sent == []
            assert (await env.sessions.current(CHAT)).session_id == row.session_id
            assert await env.dispatcher.handle(inbound("管理员的问题", user=ADMIN, addressed=True))
            await env.drain()
            assert len(env.paseo.sent) == 1
            target, command = env.paseo.sent[0]
            assert target == agent_id
            assert command.startswith("/chat_ingress ")
            assert json.loads(command[len("/chat_ingress "):])["text"] == "管理员的问题"
        finally:
            await env.close()

    asyncio.run(main())


def test_steward_assigns_inspects_and_archives_without_changing_current(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            caller, ref = await steward(env)
            assert isinstance(await env.tools.discover(ref), ToolDiscovery)
            assert await env.tools.invoke(request(ref, "self", ToolName.SESSION_ARCHIVE,
                                                  {"no": caller.no})) == ToolFailure(ToolError.SELF_ARCHIVE, 403)
            assert (await env.sessions.get(caller.session_id)).state == "discussing"

            assign = request(ref, "assign-local", ToolName.INVESTIGATION_START, {"text": "X"})
            first = await env.tools.invoke(assign)
            assert isinstance(first, ToolSuccess) and isinstance(first.data, StartReceipt)
            assert first.data.outcome is StartOutcome.CREATED and first.data.session is not None
            await env.drain()
            replay = await env.tools.invoke(assign)
            assert isinstance(replay, ToolSuccess) and isinstance(replay.data, StartReceipt)
            assert replay.data.session.session_id == first.data.session.session_id
            child = await env.sessions.by_no(CHAT, first.data.session.no)
            assert child is not None and child.scenario == "investigate"
            assert child.assigned_by == caller.session_id and child.created_by == ADMIN
            assert (await env.sessions.current(CHAT)).session_id == caller.session_id
            assert await env.sessions.by_no(CHAT, child.no + 1) is None
            assert await env.texts() == []

            remote = await env.tools.invoke(request(ref, "assign-remote", ToolName.INVESTIGATION_START,
                                                    {"text": "另一群的问题", "chat": OTHER_CHAT}))
            assert isinstance(remote, ToolSuccess) and isinstance(remote.data, StartReceipt)
            assert remote.data.session is not None
            await env.drain()
            remote_child = await env.sessions.by_no(OTHER_CHAT, remote.data.session.no)
            assert remote_child is not None and remote_child.assigned_by == caller.session_id
            assert remote_child.created_by == ADMIN and remote_child.scenario == "investigate"
            assert await env.sessions.current(OTHER_CHAT) is None

            child_agent = await env.agents.for_state(child.session_id, child.state)
            assert child_agent is not None
            accepted = await env.intake.handle({
                "agent": child_agent.agent_ref, "reply_id": "child-reply", "msgs": [],
                "reply": {"kind": "text", "text": "调查发现 X 的原因"},
            })
            assert accepted.status == 202
            delivered = await env.drain()
            assert [chat for chat, _ in delivered] == [CHAT]
            assert (await env.sessions.current(CHAT)).session_id == caller.session_id
            assert await env.sessions.current(OTHER_CHAT) is None
            shown = await env.tools.invoke(request(ref, "show", ToolName.SESSION_GET, {"no": child.no}))
            assert isinstance(shown, ToolSuccess) and isinstance(shown.data, SessionDetails)
            assert shown.data.session.session_id == child.session_id
            assert shown.data.session.is_current is False
            assert any(item.kind == "ai" and item.text == "调查发现 X 的原因" for item in shown.data.recent)

            # Exercise depth defense even if a future all-audience state exposes
            # assignment. The shipped ordinary allowlist rejects earlier.
            scenario = env.registry.scenarios[child.scenario]
            state = scenario.state(child.state)
            assert isinstance(state, AgentState)
            env.registry.scenarios[child.scenario] = replace(
                scenario, states={**scenario.states, child.state: replace(
                    state, tools=(*state.tools, ToolName.INVESTIGATION_START),
                )},
            )
            assert await env.tools.invoke(request(child_agent.agent_ref, "nested", ToolName.INVESTIGATION_START,
                                                  {"text": "不应派孙会话"})) == ToolFailure(ToolError.NESTED_ASSIGNMENT, 403)
            assert await env.sessions.by_no(CHAT, child.no + 1) is None

            chats = await env.tools.invoke(request(ref, "chats", ToolName.CHATS_LIST, {}))
            assert isinstance(chats, ToolSuccess) and isinstance(chats.data, Chats)
            assert {chat.chat_key for chat in chats.data.known} >= {CHAT, OTHER_CHAT}
            status = await env.tools.invoke(request(ref, "status", ToolName.STATUS_GET, {}))
            assert isinstance(status, ToolSuccess) and isinstance(status.data, BotStatus)
            assert status.data.sessions["active"] == 3
            archived_request = request(ref, "archive", ToolName.SESSION_ARCHIVE, {"no": child.no})
            archived = await env.tools.invoke(archived_request)
            assert isinstance(archived, ToolSuccess) and isinstance(archived.data, SessionView)
            assert archived.data.state == "archived" and archived.data.session_id == child.session_id
            assert await env.tools.invoke(archived_request) == archived
            assert (await env.sessions.get(child.session_id)).state == "archived"
            assert (await env.sessions.get(caller.session_id)).state == "discussing"
            assert child_agent.agent_id in env.paseo.archived
            assert env.paseo.created[0]["agent_id"] not in env.paseo.archived
            assert (await env.sessions.current(CHAT)).session_id == caller.session_id
            assert await env.sessions.current(OTHER_CHAT) is None
            assert await env.texts() == []
        finally:
            await env.close()

    asyncio.run(main())


def test_assignment_call_identity_includes_agent_and_never_uses_human_current(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            local_caller, local_ref = await steward(env)
            remote_caller, remote_ref = await steward(env, chat=OTHER_CHAT)
            assert await env.dispatcher.handle(inbound("/帮我调查 当前人的调查", user=USER))
            await env.drain()
            human_current = await env.sessions.current(CHAT)
            assert human_current is not None and human_current.created_by == USER

            first = await env.tools.invoke(request(local_ref, "same-id", ToolName.INVESTIGATION_START,
                                                   {"text": "相同任务"}))
            second = await env.tools.invoke(request(remote_ref, "same-id", ToolName.INVESTIGATION_START,
                                                    {"text": "相同任务", "chat": CHAT}))
            assert isinstance(first, ToolSuccess) and isinstance(first.data, StartReceipt)
            assert isinstance(second, ToolSuccess) and isinstance(second.data, StartReceipt)
            assert first.data.session is not None and second.data.session is not None
            assert first.data.session.session_id != second.data.session.session_id
            await env.drain()
            first_child = await env.sessions.get(first.data.session.session_id)
            second_child = await env.sessions.get(second.data.session.session_id)
            assert first_child is not None and second_child is not None
            assert first_child.assigned_by == local_caller.session_id
            assert second_child.assigned_by == remote_caller.session_id
            assert first_child.created_by == second_child.created_by == ADMIN
            assert first_child.input["sender"]["id"] == second_child.input["sender"]["id"] == ADMIN
            assert (await env.sessions.current(CHAT)).session_id == human_current.session_id
            assert (await env.sessions.current(OTHER_CHAT)).session_id == remote_caller.session_id
            assert await env.store.fetch_one("SELECT COUNT(*) FROM sessions") == (5,)
        finally:
            await env.close()

    asyncio.run(main())


def test_cross_chat_requires_allowed_target_and_current_admin_principal(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            caller, ref = await steward(env)
            env.tools._config = replace(env.config, allowed_chats=(CHAT,))
            denied = request(ref, "unlisted", ToolName.INVESTIGATION_START,
                             {"text": "不应分派", "chat": OTHER_CHAT})
            assert await env.tools.invoke(denied) == ToolFailure(ToolError.CHAT_NOT_ALLOWED, 403)
            assert await env.sessions.by_no(OTHER_CHAT, 1) is None
            # The session's admin audience does not retain privileges after its
            # actual creator is removed from configured administrators.
            env.tools._config = replace(env.config, admin_users=())
            assert await env.tools.invoke(request(ref, "revoked", ToolName.REQUIREMENT_ADD,
                                                  {"text": "不应跨群记录", "chat": OTHER_CHAT})) == ToolFailure(ToolError.CHAT_NOT_ALLOWED, 403)
            assert await env.tools.invoke(request(ref, "revoked-status", ToolName.STATUS_GET,
                                                  {})) == ToolFailure(ToolError.PERMISSION_DENIED, 403)
            assert (await env.requirements.list_page(OTHER_CHAT)).total == 0
            assert (await env.sessions.current(CHAT)).session_id == caller.session_id
            assert await env.texts() == []
        finally:
            await env.close()

    asyncio.run(main())


def test_management_tools_enforce_owner_and_allow_idempotent_rename(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            assert await env.dispatcher.handle(inbound("/帮我调查 自己的调查", user=USER))
            await env.drain()
            caller = await env.sessions.current(CHAT)
            assert caller is not None
            agent = await env.agents.for_state(caller.session_id, caller.state)
            assert agent is not None
            assert await env.dispatcher.handle(inbound("/帮我调查 他人的调查", user="telegram:3"))
            await env.drain()
            other = await env.sessions.current(CHAT)
            assert other is not None
            scenario = env.registry.scenarios[caller.scenario]
            state = scenario.state(caller.state)
            assert isinstance(state, AgentState)
            # Test owner checks independently of the shipped capability allowlist.
            env.registry.scenarios[caller.scenario] = replace(
                scenario, states={**scenario.states, caller.state: replace(
                    state, tools=(*state.tools, ToolName.SESSION_ARCHIVE, ToolName.SESSION_RENAME),
                )},
            )
            for name, args in (
                (ToolName.SESSION_ARCHIVE, {"no": other.no}),
                (ToolName.SESSION_RENAME, {"no": other.no, "name": "不应改名"}),
            ):
                assert await env.tools.invoke(request(agent.agent_ref, name.value, name, args)) == ToolFailure(ToolError.PERMISSION_DENIED, 403)
            assert (await env.sessions.get(other.session_id)).name == other.name
            assert (await env.sessions.get(other.session_id)).state == "discussing"
            rename = request(agent.agent_ref, "rename", ToolName.SESSION_RENAME,
                             {"no": caller.no, "name": "我的新名称"})
            renamed = await env.tools.invoke(rename)
            assert isinstance(renamed, ToolSuccess) and isinstance(renamed.data, SessionView)
            assert renamed.data.name == "我的新名称"
            assert await env.tools.invoke(rename) == renamed
            assert (await env.sessions.current(CHAT)).session_id == other.session_id
            assert await env.texts() == []
        finally:
            await env.close()

    asyncio.run(main())


def test_link_receipts_deliver_once_to_caller_without_url_in_tools_or_exchanges(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            caller, ref = await steward(env)
            assigned = await env.tools.invoke(request(ref, "remote", ToolName.INVESTIGATION_START,
                                                      {"text": "可分享的调查", "chat": OTHER_CHAT}))
            assert isinstance(assigned, ToolSuccess) and isinstance(assigned.data, StartReceipt)
            target = assigned.data.session
            assert target is not None
            await env.drain()
            link = request(ref, "link-once", ToolName.SESSION_LINK_DELIVER,
                           {"no": target.no, "chat": OTHER_CHAT})
            first, parallel_replay = await asyncio.gather(env.tools.invoke(link), env.tools.invoke(link))
            assert isinstance(first, ToolSuccess) and first.result is ToolResult.QUEUED
            assert isinstance(first.data, LinkReceipt) and first.data.session_no == target.no
            assert parallel_replay == first and await env.tools.invoke(link) == first
            encoded = json.dumps(asdict(first))
            assert "https://" not in encoded and '"url"' not in encoded and '"token"' not in encoded
            delivery_id = first.data.delivery_id
            assert await env.store.fetch_one("SELECT COUNT(*) FROM outbox WHERE reply_id = ?", (delivery_id,)) == (1,)
            assert await env.store.fetch_one("SELECT COUNT(*) FROM tokens_issued WHERE token_id = ?", (delivery_id,)) == (1,)
            assert await env.store.fetch_one("SELECT session_id,user_key,issued_by FROM tokens_issued WHERE token_id = ?",
                                             (delivery_id,)) == (target.session_id, ADMIN, ADMIN)
            queued = await OutboxRepo(env.store).get(delivery_id)
            assert queued is not None and queued.agent_ref is None
            assert queued.chat_key == CHAT and queued.session_id == caller.session_id
            assert queued.payload["type"] == "at_text" and queued.payload["platform_id"] == "1"
            assert "https://dev.example/t/" in queued.payload["text"]
            assert all(item["user_key"] == ADMIN and item["session_id"] == target.session_id
                       for item in env.gateway.issued)

            delivered = await env.drain()
            assert [chat for chat, _ in delivered] == [CHAT]
            assert any("https://dev.example/t/" in getattr(component, "text", "")
                       for component in delivered[0][1])
            assert (await OutboxRepo(env.store).get(delivery_id)).state is OutboxState.SENT
            assert await env.tools.invoke(link) == first
            assert await env.drain() == []
            for no, chat in ((caller.no, CHAT), (target.no, OTHER_CHAT)):
                shown = await env.tools.invoke(request(ref, f"recent-{chat}", ToolName.SESSION_GET,
                                                       {"no": no, "chat": chat}))
                assert isinstance(shown, ToolSuccess) and isinstance(shown.data, SessionDetails)
                assert all("https://dev.example/t/" not in item.text for item in shown.data.recent)
                assert all(item.id != f"r:{delivery_id}" for item in shown.data.recent)

            conflict = request(ref, "link-once", ToolName.SESSION_LINK_DELIVER, {"no": caller.no})
            assert await env.tools.invoke(conflict) == ToolFailure(ToolError.CALL_ID_CONFLICT, 409)
            assert await env.store.fetch_one("SELECT COUNT(*) FROM tokens_issued WHERE token_id = ?", (delivery_id,)) == (1,)
            assert await env.drain() == []
            archived = await env.tools.invoke(request(ref, "archive-target", ToolName.SESSION_ARCHIVE,
                                                      {"no": target.no, "chat": OTHER_CHAT}))
            assert isinstance(archived, ToolSuccess) and isinstance(archived.data, SessionView)
            assert archived.data.state == "archived"
            before_signing = len(env.gateway.issued)
            not_ready = await env.tools.invoke(request(ref, "ended-link", ToolName.SESSION_LINK_DELIVER,
                                                       {"no": target.no, "chat": OTHER_CHAT}))
            assert not_ready == ToolSuccess(ToolResult.NOT_READY, None)
            assert len(env.gateway.issued) == before_signing
            assert await env.drain() == []
        finally:
            await env.close()

    asyncio.run(main())
