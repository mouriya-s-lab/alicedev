"""Steward chat and agent-command flows over the real Store and scheduler."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from rt_support import ADMIN, CHAT, USER, inbound, make_env

from alicedev.actions.executor import AgentCall


OTHER_CHAT = "telegram:GroupMessage:-200"


def ingress(command: str) -> dict:
    assert command.startswith("/chat_ingress ")
    return json.loads(command[len("/chat_ingress "):])


def call(agent_ref: str, call_id: str, text: str, chat: str | None = None) -> AgentCall:
    return AgentCall(agent_ref=agent_ref, call_id=call_id, text=text, chat_key=chat, quote=None)


def test_steward_is_admin_only_and_one_per_chat(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            assert await env.dispatcher.handle(inbound("/管家 不应接收", user=USER))
            assert await env.texts() == []
            assert await env.sessions.current(CHAT) is None
            assert env.paseo.created == []

            assert await env.dispatcher.handle(inbound("/管家 你好", user=ADMIN))
            assert await env.texts() == ["已开始 %1 管家 · 你好"]
            steward = await env.sessions.by_no(CHAT, 1)
            assert steward is not None
            assert steward.scenario == "steward" and steward.created_by == ADMIN
            assert (await env.sessions.current(CHAT)).session_id == steward.session_id
            first_agent = env.paseo.created[0]

            assert await env.dispatcher.handle(inbound("/管家 再说", user=ADMIN))
            assert await env.texts() == []  # start.sent has no chat reply
            assert len(env.paseo.created) == 1
            assert await env.sessions.by_no(CHAT, 2) is None
            assert (await env.sessions.current(CHAT)).session_id == steward.session_id
            assert len(env.paseo.sent) == 1
            assert env.paseo.sent[0][0] == first_agent["agent_id"]
            assert ingress(env.paseo.sent[0][1])["text"] == "再说"

            assert await env.dispatcher.handle(inbound("/管家 另一群", user=ADMIN, chat=OTHER_CHAT))
            assert await env.texts() == ["已开始 %1 管家 · 另一群"]
            other = await env.sessions.by_no(OTHER_CHAT, 1)
            assert other is not None and other.session_id != steward.session_id
            assert other.scenario == "steward" and len(env.paseo.created) == 2
            assert (await env.sessions.current(OTHER_CHAT)).session_id == other.session_id
            assert (await env.sessions.current(CHAT)).session_id == steward.session_id
        finally:
            await env.close()

    asyncio.run(main())


def test_steward_rejects_non_admin_chat_input_silently(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            await env.dispatcher.handle(inbound("/管家 你好", user=ADMIN))
            await env.drain()
            steward = await env.sessions.by_no(CHAT, 1)
            agent_id = env.paseo.created[0]["agent_id"]

            assert await env.dispatcher.handle(inbound("问管家", user=USER, addressed=True))
            assert await env.dispatcher.handle(inbound("/继续 %1 问管家", user=USER))
            assert await env.texts() == []
            assert env.paseo.sent == []
            assert (await env.sessions.current(CHAT)).session_id == steward.session_id

            assert await env.dispatcher.handle(inbound("管理员的问题", user=ADMIN, addressed=True))
            assert await env.texts() == []
            assert len(env.paseo.sent) == 1
            assert env.paseo.sent[0][0] == agent_id
            assert ingress(env.paseo.sent[0][1])["text"] == "管理员的问题"
        finally:
            await env.close()

    asyncio.run(main())


def test_steward_assigns_and_inspects_without_changing_current(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            await env.dispatcher.handle(inbound("/管家 分诊", user=ADMIN))
            await env.drain()
            steward = await env.sessions.by_no(CHAT, 1)
            agent_ref = env.paseo.created[0]["agent_ref"]

            own = await env.dispatcher.run_agent(call(agent_ref, "own", "/归档 %1"))
            assert own.status == 403 and own.body == {"error": "self_archive"}
            assert (await env.sessions.by_no(CHAT, 1)).state == "discussing"
            assert env.paseo.archived == []

            created = await env.dispatcher.run_agent(call(agent_ref, "assign-local", "/帮我调查 X"))
            assert created.status == 200 and created.body["result"] == "created"
            child = await env.sessions.by_no(CHAT, 2)
            assert child is not None and child.scenario == "investigate"
            assert child.assigned_by == steward.session_id and child.created_by == ADMIN
            assert created.body["data"]["session"]["no"] == child.no
            assert (await env.sessions.current(CHAT)).session_id == steward.session_id

            remote = await env.dispatcher.run_agent(
                call(agent_ref, "assign-remote", "/帮我调查 另一群的问题", OTHER_CHAT)
            )
            assert remote.status == 200 and remote.body["result"] == "created"
            remote_child = await env.sessions.by_no(OTHER_CHAT, 1)
            assert remote_child is not None and remote_child.scenario == "investigate"
            assert remote_child.assigned_by == steward.session_id
            assert remote_child.created_by == ADMIN
            assert remote.body["data"]["session"]["no"] == remote_child.no
            assert await env.sessions.current(OTHER_CHAT) is None
            assert (await env.sessions.current(CHAT)).session_id == steward.session_id

            # A real child reply must be visible in the agent's session detail,
            # while neither assigned session becomes the human's current one.
            reply = await env.intake.handle({
                "agent": env.paseo.created[1]["agent_ref"], "reply_id": "child-reply",
                "msgs": [], "reply": {"kind": "text", "text": "调查发现 X 的原因"},
            })
            assert reply.status == 202
            delivered = await env.drain()
            assert [chat for chat, _ in delivered].count(OTHER_CHAT) == 1
            assert (await env.sessions.current(CHAT)).session_id == steward.session_id
            assert await env.sessions.current(OTHER_CHAT) is None


            child_ref = env.paseo.created[1]["agent_ref"]
            nested = await env.dispatcher.run_agent(call(child_ref, "nested", "/需求 y"))
            assert nested.status == 403 and nested.body == {"error": "nested_assignment"}
            assert await env.sessions.by_no(CHAT, 3) is None
            assert len(env.paseo.created) == 3

            shown = await env.dispatcher.run_agent(call(agent_ref, "show", "/会话 %2"))
            assert shown.status == 200 and shown.body["result"] == "ok"
            assert shown.body["data"]["scenario"] == "investigate"
            assert any(item["kind"] == "ai" and "调查发现 X 的原因" in item["text"]
                       for item in shown.body["data"]["recent"])
            assert shown.body["data"]["is_current"] is False

            chats = await env.dispatcher.run_agent(call(agent_ref, "chats", "/群列表"))
            assert chats.status == 200 and chats.body["result"] == "ok"
            assert {row["chat_key"] for row in chats.body["data"]["rows"]} >= {CHAT, OTHER_CHAT}
            assert all("open_sessions" in row for row in chats.body["data"]["rows"])
            status = await env.dispatcher.run_agent(call(agent_ref, "status", "/状态"))
            assert status.status == 200 and status.body["result"] == "ok"
            assert status.body["data"]["sessions_active"] == 3
            assert "agents" in status.body["data"] and "platforms" in status.body["data"]

            archived = await env.dispatcher.run_agent(call(agent_ref, "archive-child", "/归档 %2"))
            assert archived.status == 200 and archived.body["result"] == "ok"
            assert archived.body["data"]["session"]["no"] == child.no
            assert (await env.sessions.by_no(CHAT, 2)).state == "archived"
            assert (await env.sessions.by_no(CHAT, 1)).state == "discussing"
            assert env.paseo.created[1]["agent_id"] in env.paseo.archived
            assert env.paseo.created[0]["agent_id"] not in env.paseo.archived
            assert (await env.sessions.current(CHAT)).session_id == steward.session_id
            assert await env.sessions.current(OTHER_CHAT) is None
        finally:
            await env.close()

    asyncio.run(main())


def test_steward_cannot_assign_to_unlisted_chat(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            # make_env defaults to an empty allowlist (all chats allowed). Give the
            # dispatcher a restricted immutable config for this separate environment.
            env.config = replace(env.config, allowed_chats=(CHAT,))
            env.dispatcher._config = env.config
            await env.dispatcher.handle(inbound("/管家 分诊", user=ADMIN))
            await env.drain()
            steward = await env.sessions.by_no(CHAT, 1)
            agent_ref = env.paseo.created[0]["agent_ref"]

            denied = await env.dispatcher.run_agent(
                call(agent_ref, "denied-remote", "/帮我调查 不应分派", OTHER_CHAT)
            )
            assert denied.status == 403 and denied.body == {"error": "chat_not_allowed"}
            assert await env.sessions.by_no(OTHER_CHAT, 1) is None
            assert (await env.sessions.current(CHAT)).session_id == steward.session_id
            assert len(env.paseo.created) == 1
            assert await env.texts() == []
        finally:
            await env.close()

    asyncio.run(main())
