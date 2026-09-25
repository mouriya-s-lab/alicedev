"""Agent-callable commands through the real dispatcher and fake Paseo."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from rt_support import CHAT, USER, inbound, make_env

from alicedev.actions.executor import AgentCall
from alicedev.dsl.model import AgentState
from alicedev.store.exchanges_repo import ExchangesRepo
from alicedev.store.favorites_repo import FavoritesRepo


def run(coro: object) -> None:
    asyncio.run(coro)


async def started(env, *, text: str = "起点", chat: str = CHAT, user: str = USER):
    assert await env.dispatcher.handle(inbound(f"/需求 {text}", chat=chat, user=user))
    row = await env.sessions.current(chat)
    assert row is not None and row.scenario == "requirement" and row.state == "discussing"
    agent = await env.agents.for_state(row.session_id, row.state)
    assert agent is not None and agent.agent_id is not None
    await env.texts()  # Isolate each subsequent call's delivered chat output.
    return row, agent.agent_ref


def call(agent_ref: str, call_id: str, text: str, *, chat: str | None = None,
         quote: str | None = None) -> AgentCall:
    return AgentCall(agent_ref=agent_ref, call_id=call_id, text=text, chat_key=chat, quote=quote)


def test_agent_commands_expose_exact_state_permissions_and_usage(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            row, agent_ref = await started(env)
            result = await env.dispatcher.agent_commands(agent_ref)
            state = env.registry.scenarios[row.scenario].state(row.state)
            assert isinstance(state, AgentState)
            assert result.status == 200
            assert result.body["agent"] == agent_ref
            assert result.body["session_no"] == row.no
            assert result.body["chat_key"] == CHAT
            assert result.body["audience"] == "all"
            commands = result.body["commands"]
            assert [command["path"] for command in commands] == list(state.agent_commands)
            assert [(command["path"], command["usage"]) for command in commands] == [
                ("需求", "/需求 <内容>"),
                ("需求列表", "/需求列表 [页]"),
                ("收藏", "/收藏 （引用消息）"),
                ("收藏夹", "/收藏夹 [页]"),
            ]
            assert await env.texts() == []
        finally:
            await env.close()

    run(main())


def test_agent_start_attributes_to_caller_not_chat_current_and_replays_once(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            caller, agent_ref = await started(env, text="原需求")
            other, _ = await started(env, text="群里当前需求", user="telegram:3")
            assert other.session_id != caller.session_id
            assert (await env.sessions.current(CHAT)).session_id == other.session_id

            command = call(agent_ref, "start-child", "/需求 子需求")
            first = await env.dispatcher.run_agent(command)
            assert first.status == 200 and first.body["result"] == "created"
            child_no = first.body["data"]["session"]["no"]
            child = await env.sessions.by_no(CHAT, child_no)
            assert child is not None and child.session_id not in (caller.session_id, other.session_id)
            assert child.assigned_by == caller.session_id
            assert child.assigned_by != other.session_id
            assert child.created_by == caller.created_by == USER
            assert (await env.sessions.current(CHAT)).session_id == other.session_id
            assert first.body["message"]
            assert await env.texts() == [first.body["message"]]
            assert env.sender.sent[-1][0] == CHAT

            replay = await env.dispatcher.run_agent(command)
            assert replay.status == 200 and replay.body == first.body
            assert await env.texts() == []
            rows, total = await env.sessions.page(
                CHAT, page=1, scenario="requirement", include_ended=True,
                ended_states=env.scheduler.ended_states(),
            )
            assert total == 3
            assert {r.session_id for r in rows} == {
                caller.session_id, other.session_id, child.session_id
            }

            child_agent = await env.agents.for_state(child.session_id, child.state)
            assert child_agent is not None
            nested = await env.dispatcher.run_agent(
                call(child_agent.agent_ref, "nested-child", "/需求 孙需求")
            )
            assert (nested.status, nested.body["error"]) == (403, "nested_assignment")
            assert await env.sessions.by_no(CHAT, child_no + 1) is None
            assert await env.texts() == []
        finally:
            await env.close()

    run(main())


def test_agent_command_rejections_and_stale_agent(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            row, agent_ref = await started(env)
            for rejected, status, error in (
                (call(agent_ref, "not-allowed", f"/归档 %{row.no}"), 403, "command_not_allowed"),
                (call(agent_ref, "not-found", "/不存在的指令"), 400, "unknown_command"),
                (call(agent_ref, "wrong-chat", "/需求 不该跨群", chat="telegram:GroupMessage:-200"),
                 403, "chat_not_allowed"),
                (call("a_unknown", "unknown-agent", "/需求 无法执行"), 404, "agent_unknown"),
            ):
                response = await env.dispatcher.run_agent(rejected)
                assert (response.status, response.body["error"]) == (status, error)
            unknown_get = await env.dispatcher.agent_commands("a_unknown")
            assert (unknown_get.status, unknown_get.body["error"]) == (404, "agent_unknown")
            assert await env.sessions.by_no(CHAT, row.no + 1) is None
            assert await env.texts() == []

            # This state deliberately permits /会话 so missing %n reaches argument resolution,
            # rather than failing earlier as a command not allowed in the default scenario.
            scenario = env.registry.scenarios[row.scenario]
            state = scenario.state(row.state)
            assert isinstance(state, AgentState)
            env.registry.scenarios[row.scenario] = replace(
                scenario, states={**scenario.states, row.state: replace(
                    state, agent_commands=(*state.agent_commands, "会话")
                )},
            )
            usage = await env.dispatcher.run_agent(call(agent_ref, "missing-session", "/会话"))
            assert (usage.status, usage.body["error"]) == (400, "usage_error")
            assert await env.texts() == []

            assert await env.dispatcher.handle(inbound(f"/归档 %{row.no}", user=USER))
            assert (await env.sessions.get(row.session_id)).state == "archived"
            await env.texts()
            stale_get = await env.dispatcher.agent_commands(agent_ref)
            stale_run = await env.dispatcher.run_agent(call(agent_ref, "after-archive", "/需求 不应创建"))
            assert (stale_get.status, stale_get.body["error"]) == (409, "agent_not_current")
            assert (stale_run.status, stale_run.body["error"]) == (409, "agent_not_current")
            assert await env.sessions.by_no(CHAT, row.no + 1) is None
            assert await env.texts() == []
        finally:
            await env.close()

    run(main())


def test_agent_favorite_quotes_real_exchange_and_list_returns_data_not_card_outbox(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            row, agent_ref = await started(env)
            platform_text = f"/继续 %{row.no} 你好"
            assert await env.dispatcher.handle(inbound(platform_text, message_id="human-quote"))
            await env.texts()
            exchanges = ExchangesRepo(env.store)
            quote = next(item for item in await exchanges.recent(row.session_id)
                         if item.kind == "human" and item.text == platform_text)
            assert (await exchanges.get(quote.id)).text == platform_text
            favorites = FavoritesRepo(env.store)
            favorite_call = call(agent_ref, "favorite-quote", "/收藏", quote=quote.id)
            first = await env.dispatcher.run_agent(favorite_call)
            assert first.status == 200 and first.body["result"] == "ok"
            favorite_id = first.body["data"]["id"]
            saved = await favorites.get(favorite_id)
            assert saved is not None
            assert saved.saver_key == row.created_by == USER
            assert saved.author_key == quote.author_key
            assert saved.text == platform_text
            assert await env.texts() == [first.body["message"]]
            replay = await env.dispatcher.run_agent(favorite_call)
            assert replay.status == 200 and replay.body == first.body
            assert await favorites.count(CHAT) == 1
            assert await env.texts() == []

            foreign_chat = "telegram:GroupMessage:-200"
            foreign, _ = await started(env, chat=foreign_chat, text="隔壁群")
            assert await env.dispatcher.handle(inbound(
                f"/继续 %{foreign.no} 跨群内容", chat=foreign_chat, message_id="foreign-quote"
            ))
            foreign_quote = next(item for item in await exchanges.recent(foreign.session_id)
                                 if item.kind == "human" and "跨群内容" in item.text)
            assert (await exchanges.get(foreign_quote.id)).chat_key == foreign_chat
            await env.texts()
            rejected = await env.dispatcher.run_agent(
                call(agent_ref, "foreign-favorite", "/收藏", quote=foreign_quote.id)
            )
            assert (rejected.status, rejected.body["error"]) == (404, "quote_not_found")
            assert await favorites.count(CHAT) == 1
            assert await env.texts() == []

            listed = await env.dispatcher.run_agent(call(agent_ref, "list-requirements", "/需求列表"))
            assert listed.status == 200 and listed.body["result"] == "ok"
            rows = listed.body["data"]["rows"]
            assert any(item["label"] == f"%{row.no}" and item["text"] == row.name
                       and item["author_name"] == USER for item in rows)
            assert all(item["author_name"] == USER for item in rows)
            assert await env.texts() == []
            assert env.cards.rendered == []
        finally:
            await env.close()

    run(main())
