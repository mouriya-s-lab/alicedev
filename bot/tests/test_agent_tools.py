"""Typed tools over the real Store, scheduler and outbox (fake external services)."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from pathlib import Path

from rt_support import ADMIN, CHAT, USER, Env, inbound, make_env

from alicedev.agent_tools.model import ToolError, ToolFailure, ToolName, ToolRequest
from alicedev.agent_tools.parser import parse_tool_request
from alicedev.agent_tools.results import (
    RecordReceipt, SessionDetails, ToolDiscovery, ToolSuccess,
)
from alicedev.domain import JsonValue
from alicedev.dsl.model import AgentState
from alicedev.render.pagination import Page
from alicedev.store.exchanges_repo import ExchangesRepo
from alicedev.store.favorites_repo import FavoritesRepo
from alicedev.store.sessions_repo import SessionRow

OTHER_CHAT = "telegram:GroupMessage:-200"


def request(agent_ref: str, call_id: str, name: ToolName,
            args: dict[str, JsonValue]) -> ToolRequest:
    parsed = parse_tool_request(name.value, {
        "agent": agent_ref, "call_id": call_id, "args": args,
    })
    assert isinstance(parsed, ToolRequest), parsed
    return parsed


async def started(env: Env, *, text: str = "起点", chat: str = CHAT,
                  user: str = USER) -> tuple[SessionRow, str]:
    assert await env.dispatcher.handle(inbound(f"/帮我调查 {text}", chat=chat, user=user))
    await env.drain()
    row = await env.sessions.current(chat)
    assert row is not None and row.scenario == "investigate" and row.state == "discussing"
    agent = await env.agents.for_state(row.session_id, row.state)
    assert agent is not None and agent.agent_id is not None
    return row, agent.agent_ref


def test_tool_permissions_explicit_target_and_archived_caller(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            caller, ref = await started(env)
            current, admin_ref = await started(env, text="当前调查", user=ADMIN)
            discovered = await env.tools.discover(ref)
            assert isinstance(discovered, ToolDiscovery)
            assert discovered.session_no == caller.no and discovered.chat_key == CHAT
            assert ToolName.REQUIREMENT_ADD in {tool.name for tool in discovered.tools}
            assert ToolName.SESSION_ARCHIVE not in {tool.name for tool in discovered.tools}

            # A tool's explicit number wins over the human's current session.
            shown = await env.tools.invoke(request(ref, "explicit", ToolName.SESSION_GET,
                                                  {"no": caller.no}))
            assert isinstance(shown, ToolSuccess) and isinstance(shown.data, SessionDetails)
            assert shown.data.session.session_id == caller.session_id
            assert shown.data.session.is_current is False
            assert (await env.sessions.current(CHAT)).session_id == current.session_id
            missing = parse_tool_request("session_get", {"agent": ref, "call_id": "missing", "args": {}})
            assert missing == ToolFailure(ToolError.INVALID_PAYLOAD, 400)

            for rejected, expected in (
                (request(ref, "archive", ToolName.SESSION_ARCHIVE, {"no": current.no}),
                 ToolFailure(ToolError.TOOL_NOT_ALLOWED, 403)),
                (request(ref, "cross-chat", ToolName.REQUIREMENT_ADD,
                         {"text": "越权", "chat": OTHER_CHAT}),
                 ToolFailure(ToolError.CHAT_NOT_ALLOWED, 403)),
                # Being an admin is insufficient without an admin-audience scenario.
                (request(admin_ref, "admin-cross-chat", ToolName.REQUIREMENT_ADD,
                         {"text": "越权", "chat": OTHER_CHAT}),
                 ToolFailure(ToolError.CHAT_NOT_ALLOWED, 403)),
                (request("a_unknown", "unknown", ToolName.REQUIREMENT_ADD, {"text": "越权"}),
                 ToolFailure(ToolError.AGENT_UNKNOWN, 404)),
            ):
                assert await env.tools.invoke(rejected) == expected
            assert await env.tools.discover("a_unknown") == ToolFailure(ToolError.AGENT_UNKNOWN, 404)
            assert (await env.requirements.list_page(OTHER_CHAT)).total == 0
            assert (await env.requirements.list_page(CHAT)).total == 0
            assert (await env.sessions.get(current.session_id)).state == "discussing"
            assert await env.texts() == []

            assert await env.dispatcher.handle(inbound(f"/归档 %{caller.no}", user=USER))
            await env.drain()
            assert (await env.sessions.get(caller.session_id)).state == "archived"
            assert await env.tools.discover(ref) == ToolFailure(ToolError.AGENT_NOT_CURRENT, 409)
            assert await env.tools.invoke(request(ref, "stale", ToolName.REQUIREMENT_ADD,
                                                  {"text": "不应记录"})) == ToolFailure(ToolError.AGENT_NOT_CURRENT, 409)
            assert (await env.requirements.list_page(CHAT)).total == 0
            assert await env.texts() == []
        finally:
            await env.close()

    asyncio.run(main())


def test_superseded_agent_cannot_read_or_write_same_state(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            caller, old_ref = await started(env)
            # Simulate a replacement agent in the same state: no public chat action
            # creates this recovery condition, so only that prerequisite is seeded.
            await env.agents.create_creating(agent_ref="a_replacement", session_id=caller.session_id,
                                            state=caller.state, provider="omp-alicedev")
            assert await env.tools.discover(old_ref) == ToolFailure(ToolError.AGENT_NOT_CURRENT, 409)
            assert await env.tools.invoke(request(old_ref, "superseded", ToolName.REQUIREMENT_ADD,
                                                  {"text": "旧 agent 不应写入"})) == ToolFailure(ToolError.AGENT_NOT_CURRENT, 409)
            assert (await env.requirements.list_page(CHAT)).total == 0
            assert (await env.sessions.current(CHAT)).session_id == caller.session_id
            assert await env.texts() == []
        finally:
            await env.close()

    asyncio.run(main())


def test_requirements_and_favorites_keep_real_quotes_principal_and_retry_identity(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            caller, ref = await started(env)
            current, other_ref = await started(env, text="他人的调查", user="telegram:3")
            quote_text = f"/继续 %{caller.no} 原始引用正文"
            assert await env.dispatcher.handle(inbound(quote_text, user="telegram:3", message_id="human-quote"))
            await env.drain()
            # Keep the other human session current while invoking the first agent.
            assert await env.dispatcher.handle(inbound(f"/继续 %{current.no} 当前会话", user="telegram:3"))
            await env.drain()
            exchanges = ExchangesRepo(env.store)
            quote = next(item for item in await exchanges.recent(caller.session_id)
                         if item.kind == "human" and item.text == quote_text)
            favorites = FavoritesRepo(env.store)

            add = request(ref, "shared-call-id", ToolName.REQUIREMENT_ADD,
                          {"text": "独立需求", "quote": quote.id})
            first = await env.tools.invoke(add)
            assert isinstance(first, ToolSuccess) and isinstance(first.data, RecordReceipt)
            assert await env.tools.invoke(add) == first
            record = await env.requirements.get(first.data.id)
            assert record is not None and record.author_key == USER and record.author_name == "user2"
            assert record.text == "独立需求" and record.quoted is not None
            assert (record.quoted.author_key, record.quoted.author_name, record.quoted.text) == (
                quote.author_key, quote.author_name, quote.text,
            )
            assert record.quoted.images == quote.images

            save = request(ref, "shared-call-id", ToolName.FAVORITE_ADD, {"quote": quote.id})
            saved = await env.tools.invoke(save)
            assert isinstance(saved, ToolSuccess) and isinstance(saved.data, RecordReceipt)
            assert await env.tools.invoke(save) == saved
            favorite = await favorites.get(saved.data.id)
            assert favorite is not None and favorite.saver_key == USER
            assert (favorite.author_key, favorite.author_name, favorite.text) == (
                quote.author_key, quote.author_name, quote.text,
            )
            assert favorite.images == quote.images

            # A call ID is local to its agent, not a global deduplication key.
            other_add = await env.tools.invoke(request(other_ref, "shared-call-id", ToolName.REQUIREMENT_ADD,
                                                       {"text": "独立需求", "quote": quote.id}))
            other_save = await env.tools.invoke(request(other_ref, "shared-call-id", ToolName.FAVORITE_ADD,
                                                        {"quote": quote.id}))
            assert isinstance(other_add, ToolSuccess) and isinstance(other_add.data, RecordReceipt)
            assert isinstance(other_save, ToolSuccess) and isinstance(other_save.data, RecordReceipt)
            assert other_add.data.id != first.data.id and other_save.data.id != saved.data.id
            assert (await env.requirements.get(other_add.data.id)).author_key == "telegram:3"
            assert (await favorites.get(other_save.data.id)).saver_key == "telegram:3"

            requirement_list = await env.tools.invoke(request(ref, "requirements", ToolName.REQUIREMENTS_LIST, {}))
            favorite_list = await env.tools.invoke(request(ref, "favorites", ToolName.FAVORITES_LIST, {}))
            assert isinstance(requirement_list, ToolSuccess) and isinstance(requirement_list.data, Page)
            assert isinstance(favorite_list, ToolSuccess) and isinstance(favorite_list.data, Page)
            assert {item.id for item in requirement_list.data.items} == {first.data.id, other_add.data.id}
            assert {item.text for item in requirement_list.data.items} == {"独立需求"}
            assert {item.id for item in favorite_list.data.items} == {saved.data.id, other_save.data.id}
            assert {item.text for item in favorite_list.data.items} == {quote_text}
            assert (await env.sessions.current(CHAT)).session_id == current.session_id
            assert await env.texts() == []

            foreign, _ = await started(env, chat=OTHER_CHAT, text="另一个群")
            foreign_quote = next(item for item in await exchanges.recent(foreign.session_id) if item.kind == "human")
            for name, args in (
                (ToolName.FAVORITE_ADD, {"quote": foreign_quote.id}),
                (ToolName.REQUIREMENT_ADD, {"text": "越界引用", "quote": foreign_quote.id}),
            ):
                assert await env.tools.invoke(request(ref, f"foreign-{name.value}", name, args)) == ToolFailure(ToolError.QUOTE_NOT_FOUND, 404)
            assert (await env.requirements.list_page(CHAT)).total == 2
            assert await favorites.count(CHAT) == 2
            assert await env.texts() == []
        finally:
            await env.close()

    asyncio.run(main())


def test_chat_requirement_records_without_starting_ai_or_changing_current(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            current, ref = await started(env)
            before_sessions = await env.store.fetch_one("SELECT COUNT(*) FROM sessions")
            before_agents = await env.store.fetch_one("SELECT COUNT(*) FROM agents")
            message = inbound("/需求 相同正文", message_id="requirement-one")
            assert await env.dispatcher.handle(message)
            assert await env.dispatcher.handle(message)
            assert await env.dispatcher.handle(inbound("/需求 相同正文", message_id="requirement-two"))
            await env.drain()
            assert await env.store.fetch_one("SELECT COUNT(*) FROM sessions") == before_sessions
            assert await env.store.fetch_one("SELECT COUNT(*) FROM agents") == before_agents
            assert (await env.sessions.current(CHAT)).session_id == current.session_id
            records = await env.tools.invoke(request(ref, "chat-records", ToolName.REQUIREMENTS_LIST, {}))
            assert isinstance(records, ToolSuccess) and isinstance(records.data, Page)
            assert records.data.total == 2
            assert {item.text for item in records.data.items} == {"相同正文"}
            assert {item.source.ref for item in records.data.items} == {"requirement-one", "requirement-two"}
            favorites = await env.tools.invoke(request(ref, "no-favorites", ToolName.FAVORITES_LIST, {}))
            assert isinstance(favorites, ToolSuccess) and isinstance(favorites.data, Page)
            assert favorites.data.items == [] and favorites.data.total == 0
        finally:
            await env.close()

    asyncio.run(main())


def test_tools_preserve_real_ai_exchange_image_bytes_and_absent_author_identity(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            caller, ref = await started(env)
            scenario = env.registry.scenarios[caller.scenario]
            state = scenario.state(caller.state)
            assert isinstance(state, AgentState) and state.reply is not None
            # Create the image-bearing Exchange through actual reply intake,
            # not an invented human attachment fact or direct outbox seed.
            env.registry.scenarios[caller.scenario] = replace(
                scenario, states={**scenario.states, caller.state: replace(
                    state, reply=replace(state.reply, kinds=(*state.reply.kinds, "image")),
                )},
            )
            image_bytes = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a30sAAAAASUVORK5CYII="
            )
            image = env.config.reports_root / "quoted.png"
            image.write_bytes(image_bytes)
            accepted = await env.intake.handle({
                "agent": ref, "reply_id": "image-exchange", "msgs": [],
                "reply": {"kind": "image", "paths": [str(image)], "caption": "真实图片引用"},
            })
            assert accepted.status == 202
            delivered = await env.drain()
            assert [chat for chat, _ in delivered] == [CHAT]
            quote = await ExchangesRepo(env.store).get("image-exchange")
            assert quote is not None and quote.kind == "ai" and quote.author_key is None
            assert quote.text == "真实图片引用" and quote.images == (str(image),)

            add = request(ref, "image-requirement", ToolName.REQUIREMENT_ADD,
                          {"text": "带图片的需求", "quote": quote.id})
            save = request(ref, "image-favorite", ToolName.FAVORITE_ADD, {"quote": quote.id})
            added = await env.tools.invoke(add)
            saved = await env.tools.invoke(save)
            assert isinstance(added, ToolSuccess) and isinstance(added.data, RecordReceipt)
            assert isinstance(saved, ToolSuccess) and isinstance(saved.data, RecordReceipt)
            assert await env.tools.invoke(add) == added and await env.tools.invoke(save) == saved
            requirement = await env.requirements.get(added.data.id)
            favorite = await FavoritesRepo(env.store).get(saved.data.id)
            assert requirement is not None and requirement.quoted is not None and favorite is not None
            assert requirement.author_key == favorite.saver_key == USER
            assert requirement.quoted.author_key is None
            assert favorite.author_key == f"alicedev:s{caller.session_id}"
            assert requirement.quoted.author_name == favorite.author_name == quote.author_name
            assert requirement.quoted.text == favorite.text == quote.text
            for stored_images in (requirement.quoted.images, favorite.images):
                assert tuple(Path(path).read_bytes() for path in stored_images) == (image_bytes,)
                assert all(Path(path).parent == env.config.images_root for path in stored_images)
            assert (await env.requirements.list_page(CHAT)).total == 1
            assert await FavoritesRepo(env.store).count(CHAT) == 1
            assert await env.texts() == []
        finally:
            await env.close()

    asyncio.run(main())
