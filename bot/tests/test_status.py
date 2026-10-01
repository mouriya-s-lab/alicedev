"""Shared status snapshots over real repository data."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import alicedev.status as status_module
from rt_support import make_env

from alicedev.dsl.model import (
    Command,
    CommandType,
    DslError,
    HelpAction,
    Permission,
    Registry,
)
from alicedev.outbox.service import digest
from alicedev.status import collect_status, status_json
from alicedev.store.agents_repo import AgentRowStatus
from alicedev.store.outbox_repo import OutboxState


def _command(name: str) -> Command:
    return Command(
        name=name,
        type=CommandType.PROGRAM,
        summary="",
        usage="",
        params=(),
        examples=(),
        permission=Permission.ALL,
        do=HelpAction(command=None),
        say={},
    )


def test_status_snapshot_matches_expected_store_state(tmp_path: Path, monkeypatch) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            rows = (
                (1, "investigate", "discussing"),
                (2, "investigate", "queued"),
                (3, "investigate", "failed"),
                (4, "upgrade-bot", "active"),
            )
            async with env.store.lock:
                for session_id, scenario, state in rows:
                    await env.store.execute(
                        "INSERT INTO sessions "
                        "(session_id, chat_key, no, scenario, name, created_by, input, state, data) "
                        "VALUES (?, 'telegram:status', ?, ?, ?, 'tester', '{}', ?, '{}')",
                        (session_id, session_id, scenario, f"session {session_id}", state),
                    )

                for agent_ref, session_id in (("active-agent", 1), ("closed-agent", 2),
                                              ("failed-agent", 3)):
                    await env.agents.create_creating(
                        agent_ref=agent_ref,
                        session_id=session_id,
                        state="discussing",
                        provider="test",
                    )
                await env.agents.set_active(
                    "active-agent", agent_id="active-id", workspace_id=None, server_id=None
                )
                await env.agents.set_status("closed-agent", AgentRowStatus.CLOSED)
                await env.agents.set_status("failed-agent", AgentRowStatus.FAILED)

                for reply_id, state in (("queued-reply", OutboxState.QUEUED),
                                         ("sent-reply", OutboxState.SENT)):
                    payload = {"type": "text", "text": reply_id}
                    await env.outbox.repo.insert(
                        reply_id=reply_id,
                        chat_key="telegram:status",
                        session_id=1,
                        agent_ref=None,
                        msgs=(),
                        payload=payload,
                        payload_sha256=digest(payload),
                        state=state,
                    )

            alpha, zeta = _command("alpha"), _command("zeta")
            registry = Registry(
                commands={"zeta": zeta, "alpha_alias": alpha, "alpha": alpha},
                routes=(),
                scenarios={
                    "upgrade-bot": env.registry.scenarios["upgrade-bot"],
                    "investigate": env.registry.scenarios["investigate"],
                },
                messages={},
                errors=(DslError("templates/broken.yaml", 7, "invalid command"),),
            )
            monkeypatch.setattr(
                status_module, "time", SimpleNamespace(monotonic=lambda: 90.9)
            )
            result = await collect_status(
                registry=registry,
                sessions=env.sessions,
                agents=env.agents,
                outbox=env.outbox,
                platforms_fn=lambda: ["telegram", "qq"],
                generation=7,
                revision="deadbeef",
                started_at=65.5,
                ended_states=env.scheduler.ended_states(),
            )

            assert status_json(result) == {
                "generation": 7,
                "revision": "deadbeef",
                "uptime_s": 25,
                "platforms": ["telegram", "qq"],
                "commands": ["alpha", "zeta"],
                "scenarios": ["investigate", "upgrade-bot"],
                "dsl_errors": [
                    {"path": "templates/broken.yaml", "line": 7, "message": "invalid command"}
                ],
                "sessions": {"active": 1, "queued": 1},
                "agents": {"active": 1, "closed": 1},
                "outbox_pending": 1,
            }
        finally:
            await env.close()

    asyncio.run(main())
