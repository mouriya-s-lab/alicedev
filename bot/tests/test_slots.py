"""Live-agent cap (ARCHITECTURE §4 在线名额): admission, eviction, waiting, waking."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rt_support import CHAT, make_env, inbound

from alicedev.paseo.control import AgentStatus

CAP = 4


def run(coro):
    return asyncio.run(coro)


async def _sessions(env, n: int, *, prefix: str = "t") -> None:
    for i in range(n):
        await env.dispatcher.handle(inbound(f"/帮我调查 {prefix}{i}", message_id=f"{prefix}{i}"))
    await env.drain()


def _live(env) -> int:
    return sum(
        1 for i, s in env.paseo.statuses.items() if s is not AgentStatus.CLOSED and i not in env.paseo.archived
    )


def test_concurrent_starts_never_exceed_cap_and_the_rest_wait(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        env.paseo.create_status = AgentStatus.RUNNING  # nothing can be parked
        await asyncio.gather(*(
            env.dispatcher.handle(inbound(f"/帮我调查 c{i}", message_id=f"c{i}")) for i in range(CAP + 2)
        ))
        texts = await env.texts()
        assert len(env.paseo.created) == CAP and _live(env) == CAP
        assert sum("在线会话已满" in t for t in texts) == 2
        waiting = [s for s in await env.sessions.in_states(("queued",))]
        assert len(waiting) == 2 and [s.no for s in waiting] == sorted(s.no for s in waiting)

        # A slot frees up: exactly the oldest waiting session starts, FIFO.
        first = env.paseo.created[0]["agent_id"]
        env.paseo.statuses[first] = AgentStatus.CLOSED
        await env.scheduler.retry_waiting()
        assert len(env.paseo.created) == CAP + 1
        assert (await env.sessions.get(waiting[0].session_id)).state == "discussing"
        assert (await env.sessions.get(waiting[1].session_id)).state == "queued"
        await env.close()

    run(main())


def test_full_pool_parks_the_least_recently_active_idle_agent(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        await _sessions(env, CAP)
        oldest = env.paseo.created[0]["agent_id"]
        await _sessions(env, 1, prefix="x")
        assert env.paseo.parked == [oldest]
        assert len(env.paseo.created) == CAP + 1 and _live(env) == CAP
        row = await env.agents.get(env.paseo.created[0]["agent_ref"])
        assert row.status.value == "closed"
        await env.close()

    run(main())


def test_unknown_paseo_state_refuses_admission(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        env.paseo.fail_live = True
        await env.dispatcher.handle(inbound("/帮我调查 q", message_id="q1"))
        assert env.paseo.created == []
        assert (await env.sessions.by_no(CHAT, 1)).state == "queued"
        assert any("在线会话已满" in t for t in await env.texts())

        env.paseo.fail_live = False
        await env.scheduler.retry_waiting()
        assert len(env.paseo.created) == 1
        assert (await env.sessions.by_no(CHAT, 1)).state == "discussing"
        await env.close()

    run(main())


def test_waking_a_parked_session_needs_a_slot_and_keeps_the_message_out_when_full(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        await _sessions(env, CAP)
        parked_id = env.paseo.created[0]["agent_id"]
        env.paseo.statuses[parked_id] = AgentStatus.CLOSED  # parked by paseo/sweeper
        await _sessions(env, 1, prefix="y")  # takes the freed slot
        for agent_id in list(env.paseo.statuses):
            if env.paseo.statuses[agent_id] is not AgentStatus.CLOSED:
                env.paseo.statuses[agent_id] = AgentStatus.RUNNING
        sent_before = len(env.paseo.sent)

        await env.dispatcher.handle(inbound("/继续 %1 醒醒", message_id="w1"))
        assert await env.texts() == ["%1 暂时唤醒不了：在线会话已满，稍后再试。"]
        assert len(env.paseo.sent) == sent_before  # nothing reached the parked agent

        newest = env.paseo.created[-1]["agent_id"]
        env.paseo.statuses[newest] = AgentStatus.IDLE
        await env.dispatcher.handle(inbound("/继续 %1 醒醒", message_id="w2"))
        await env.drain()
        assert env.paseo.parked == [newest]
        assert env.paseo.sent[-1][0] == parked_id and "醒醒" in env.paseo.sent[-1][1]
        assert env.paseo.statuses[parked_id] is AgentStatus.IDLE
        await env.close()

    run(main())


def test_sweeper_parks_idle_agents_and_records_ones_paseo_already_archived(tmp_path: Path) -> None:
    from alicedev.paseo.sweeper import IdleSweeper

    async def main() -> None:
        env = await make_env(tmp_path)
        await _sessions(env, 2)
        idle_id, gone_id = env.paseo.created[0]["agent_id"], env.paseo.created[1]["agent_id"]
        env.paseo.statuses[gone_id] = AgentStatus.CLOSED  # someone archived it behind the bot's back
        await env.store.execute(
            "UPDATE agents SET last_activity_at = last_activity_at - INTERVAL 2 HOUR"
        )
        sweeper = IdleSweeper(
            agents=env.agents, actor=env.scheduler._actor, config=env.config,
            is_conversational=env.scheduler.is_conversational_agent,
        )
        assert await sweeper.sweep_once() == 2
        assert env.paseo.parked == [idle_id]  # the already-archived one is only recorded
        assert {r.status.value for r in await env.agents.by_agent_ids([idle_id, gone_id])} == {"closed"}
        await env.close()

    run(main())
