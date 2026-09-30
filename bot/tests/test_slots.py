"""Live-agent cap (ARCHITECTURE §4 在线名额): admission, eviction, waiting, waking."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rt_support import ADMIN, CHAT, make_env, inbound

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


def test_new_session_queues_behind_older_waiters_even_when_a_slot_is_free(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        env.paseo.create_status = AgentStatus.RUNNING
        await _sessions(env, CAP + 2, prefix="f")
        waiting = await env.sessions.in_states(("queued",))
        assert len(waiting) == 2

        # A slot frees up, then a brand-new session arrives before the waiter loop runs.
        env.paseo.statuses[env.paseo.created[0]["agent_id"]] = AgentStatus.CLOSED
        await env.dispatcher.handle(inbound("/帮我调查 late", message_id="late"))
        await env.drain()

        states = {s.no: s.state for s in [await env.sessions.get(w.session_id) for w in waiting]}
        newest = await env.sessions.by_no(CHAT, CAP + 3)
        assert states[waiting[0].no] == "discussing"  # the oldest waiter took the free slot
        assert states[waiting[1].no] == "queued" and newest.state == "queued"
        assert len(env.paseo.created) == CAP + 1
        await env.close()

    run(main())


def test_recover_admits_older_waiter_before_newer_exclusive_scenario(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            env.paseo.create_status = AgentStatus.RUNNING
            await _sessions(env, CAP + 1, prefix="recover")
            await env.dispatcher.handle(inbound(
                "/升级bot newer", user=ADMIN, message_id="recover-upgrade",
            ))
            await env.drain()
            older = await env.sessions.by_no(CHAT, CAP + 1)
            newer = await env.sessions.by_no(CHAT, CAP + 2)
            assert older is not None and newer is not None
            assert older.scenario == "investigate" and newer.scenario == "upgrade-bot"
            assert older.state == newer.state == "queued"
            assert len(env.paseo.created) == CAP and _live(env) == CAP

            # Recovery sees a persisted cross-scenario queue and exactly one free runtime.
            await env.scheduler.stop()
            env.paseo.statuses[env.paseo.created[0]["agent_id"]] = AgentStatus.CLOSED
            await env.scheduler.recover()
            await env.drain()

            admitted = await env.agents.get(env.paseo.created[-1]["agent_ref"])
            assert admitted is not None and admitted.session_id == older.session_id
            assert admitted.agent_id == env.paseo.created[-1]["agent_id"]
            assert admitted.status.value == "active"
            assert (await env.sessions.get(older.session_id)).state == "discussing"
            assert (await env.sessions.get(newer.session_id)).state == "queued"
            assert await env.agents.unarchived(newer.session_id) == []
            assert len(env.paseo.created) == CAP + 1 and _live(env) == CAP
        finally:
            await env.close()

    run(main())


def test_cross_scenario_start_cannot_overtake_waiter_admission_in_flight(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        older_paused = asyncio.Event()
        release_older = asyncio.Event()
        contender_observed = asyncio.Event()
        tasks: list[asyncio.Task] = []
        newcomer: asyncio.Task | None = None
        original_unarchived = env.agents.unarchived
        try:
            env.paseo.create_status = AgentStatus.RUNNING
            await _sessions(env, CAP + 1, prefix="race")
            await env.scheduler.stop()
            older = await env.sessions.by_no(CHAT, CAP + 1)
            assert older is not None and older.state == "queued"
            assert len(env.paseo.created) == CAP and _live(env) == CAP
            env.paseo.statuses[env.paseo.created[0]["agent_id"]] = AgentStatus.CLOSED

            async def paused_unarchived(session_id: int):
                agents = await original_unarchived(session_id)
                if session_id == older.session_id:
                    # Real Store query: the waiter has left queued, but has no agent yet.
                    older_paused.set()
                    await release_older.wait()
                elif asyncio.current_task() is newcomer:
                    # Without shared admission serialization the newcomer gets this far.
                    contender_observed.set()
                return agents

            class ObservedDispatchLock(asyncio.Lock):
                async def acquire(self) -> bool:
                    if asyncio.current_task() is newcomer:
                        # Rendezvous even when correct serialization blocks the newcomer.
                        contender_observed.set()
                    return await super().acquire()

            env.agents.unarchived = paused_unarchived
            env.scheduler._dispatch_lock = ObservedDispatchLock()
            retry = asyncio.create_task(env.scheduler.retry_waiting())
            tasks.append(retry)
            await older_paused.wait()
            assert (await env.sessions.get(older.session_id)).state == "discussing"
            assert await original_unarchived(older.session_id) == []
            assert _live(env) == CAP - 1

            newcomer = asyncio.create_task(env.dispatcher.handle(inbound(
                "/升级bot contender", user=ADMIN, message_id="race-upgrade",
            )))
            tasks.append(newcomer)
            await contender_observed.wait()
            newer = await env.sessions.by_no(CHAT, CAP + 2)
            assert newer is not None and newer.scenario == "upgrade-bot"
            # Assert persisted behavior, not whether either synchronization hook was called.
            assert newer.state == "queued"
            assert await original_unarchived(newer.session_id) == []

            release_older.set()
            await asyncio.gather(*tasks)
            await env.drain()
            admitted = await env.agents.get(env.paseo.created[-1]["agent_ref"])
            assert admitted is not None and admitted.session_id == older.session_id
            assert admitted.agent_id == env.paseo.created[-1]["agent_id"]
            assert admitted.status.value == "active"
            assert (await env.sessions.get(older.session_id)).state == "discussing"
            assert (await env.sessions.get(newer.session_id)).state == "queued"
            assert await original_unarchived(newer.session_id) == []
            assert len(env.paseo.created) == CAP + 1 and _live(env) == CAP
        finally:
            release_older.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            env.agents.unarchived = original_unarchived
            await env.close()

    run(asyncio.wait_for(main(), timeout=10))


def test_retry_admitted_new_request_is_not_dispatched_again_by_its_caller(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        first_paused = asyncio.Event()
        release_first = asyncio.Event()
        retry_waiting = asyncio.Event()
        newcomer_waiting = asyncio.Event()
        tasks: list[asyncio.Task] = []
        first: asyncio.Task | None = None
        retry: asyncio.Task | None = None
        newcomer: asyncio.Task | None = None
        original_unarchived = env.agents.unarchived
        try:
            env.paseo.create_status = AgentStatus.RUNNING

            async def paused_unarchived(session_id: int):
                agents = await original_unarchived(session_id)
                if asyncio.current_task() is first:
                    first_paused.set()
                    await release_first.wait()
                return agents

            class ObservedDispatchLock(asyncio.Lock):
                async def acquire(self) -> bool:
                    if asyncio.current_task() is retry:
                        retry_waiting.set()
                    elif asyncio.current_task() is newcomer:
                        newcomer_waiting.set()
                    return await super().acquire()

            env.agents.unarchived = paused_unarchived
            env.scheduler._dispatch_lock = ObservedDispatchLock()
            first = asyncio.create_task(env.dispatcher.handle(inbound(
                "/帮我调查 first", message_id="stale-first",
            )))
            tasks.append(first)
            await first_paused.wait()
            older = await env.sessions.by_no(CHAT, 1)
            assert older is not None and older.state == "discussing"
            assert await original_unarchived(older.session_id) == []

            # Queue the retry before the second caller, while admission 1 owns the lock.
            retry = asyncio.create_task(env.scheduler.retry_waiting())
            tasks.append(retry)
            await retry_waiting.wait()
            newcomer = asyncio.create_task(env.dispatcher.handle(inbound(
                "/升级bot second", user=ADMIN, message_id="stale-second",
            )))
            tasks.append(newcomer)
            await newcomer_waiting.wait()
            newer = await env.sessions.by_no(CHAT, 2)
            assert newer is not None and newer.scenario == "upgrade-bot"
            assert newer.state == "queued"
            assert env.paseo.created == []

            # Retry acquires next and admits session 2 before its original caller resumes.
            release_first.set()
            await asyncio.gather(*tasks)
            await env.drain()
            admitted = [
                await env.agents.get(created["agent_ref"]) for created in env.paseo.created
            ]
            assert [agent.session_id for agent in admitted] == [older.session_id, newer.session_id]
            current = await original_unarchived(newer.session_id)
            assert len(current) == 1
            assert current[0].agent_id == env.paseo.created[1]["agent_id"]
            assert current[0].status.value == "active"
            assert (await env.sessions.get(older.session_id)).state == "discussing"
            assert (await env.sessions.get(newer.session_id)).state == "working"
            assert _live(env) == 2
            assert _live(env) <= CAP
        finally:
            release_first.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            env.agents.unarchived = original_unarchived
            await env.close()

    run(asyncio.wait_for(main(), timeout=10))


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
