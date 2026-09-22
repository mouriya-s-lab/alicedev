from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alicedev.paseo.control import AgentStatus, WaitOutcome, WaitResult, WorktreeRef  # noqa: E402
from alicedev.store.upgrade_runs_repo import UpgradeRunRecord, UpgradeRunStatus  # noqa: E402
from alicedev.upgrade.conductor import (  # noqa: E402
    CandidateReadyPayload,
    DeployResultPayload,
    E2ENotConvergingPayload,
    E2ERound,
    MainSyncFailedPayload,
    MainSyncFailure,
    RenderedEvidence,
    UpgradeConductor,
)
from alicedev.upgrade.convergence import RoundSummary  # noqa: E402
from alicedev.upgrade.state import RunState  # noqa: E402


RUN_ID = "ur_test"


class FakeRepo:
    def __init__(self, *, status: UpgradeRunStatus = UpgradeRunStatus.ACCEPTED) -> None:
        self.record = UpgradeRunRecord(
            run_id=RUN_ID,
            chat_key="chat",
            user_key="user",
            workspace_ref=f"upgrade/{RUN_ID}",
            status=status,
            candidate_commit=None,
            evidence_refs={},
            created_at=None,
            updated_at=None,
        )
        self.transitions: list[UpgradeRunStatus] = [status]

    async def latest_for_chat(self, chat_key: str) -> UpgradeRunRecord | None:
        return self.record if self.record.chat_key == chat_key else None

    async def get(self, run_id: str) -> UpgradeRunRecord | None:
        return self.record if run_id == self.record.run_id else None

    def _set(self, status: UpgradeRunStatus, *, commit: str | None = None, evidence: dict[str, str] | None = None) -> bool:
        self.record = UpgradeRunRecord(
            run_id=self.record.run_id,
            chat_key=self.record.chat_key,
            user_key=self.record.user_key,
            workspace_ref=self.record.workspace_ref,
            status=status,
            candidate_commit=commit if commit is not None else self.record.candidate_commit,
            evidence_refs=evidence if evidence is not None else self.record.evidence_refs,
            created_at=self.record.created_at,
            updated_at=self.record.updated_at,
        )
        self.transitions.append(status)
        return True

    async def mark_candidate_ready(self, run_id: str, *, commit: str, evidence_refs: dict[str, str]) -> bool:
        assert run_id == RUN_ID
        return self._set(UpgradeRunStatus.CANDIDATE_READY, commit=commit, evidence=evidence_refs)

    async def mark_awaiting_approval(self, run_id: str) -> bool:
        assert run_id == RUN_ID
        return self._set(UpgradeRunStatus.AWAITING_APPROVAL)

    async def mark_deploying(self, run_id: str) -> bool:
        assert run_id == RUN_ID
        return self._set(UpgradeRunStatus.DEPLOYING)

    async def mark_deploy_result(self, run_id: str, outcome: str) -> bool:
        assert run_id == RUN_ID
        status = {
            "active": UpgradeRunStatus.ACTIVE,
            "rolled_back": UpgradeRunStatus.ROLLED_BACK,
            "rollback_failed": UpgradeRunStatus.FAILED,
        }[outcome]
        return self._set(status)

    async def mark_main_sync_failed(self, run_id: str) -> bool:
        assert run_id == RUN_ID
        return self._set(UpgradeRunStatus.MAIN_SYNC_FAILED)

    async def mark_failed(self, run_id: str) -> bool:
        assert run_id == RUN_ID
        return self._set(UpgradeRunStatus.FAILED)

    async def reject(self, run_id: str) -> bool:
        assert run_id == RUN_ID
        return self._set(UpgradeRunStatus.FAILED)


class FakeDaemon:
    def __init__(self, *, wait_outcome: WaitOutcome = WaitOutcome.IDLE) -> None:
        self.wait_outcome = wait_outcome
        self.agents: list[dict[str, Any]] = []
        self.archived: list[str] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def aclose(self) -> None:
        self.connected = False

    async def worktree_create(self, *, repo_path: str, base_ref: str, name: str) -> WorktreeRef:
        assert repo_path == "/fixed-main"
        assert base_ref == "aligned-sha"
        assert name == f"upgrade/{RUN_ID}"
        return WorktreeRef("workspace-test", "/worktree-test")

    async def worktree_archive(self, workspace_id: str) -> None:
        self.archived.append(workspace_id)

    async def agent_create(self, **kwargs: Any) -> str:
        self.agents.append(kwargs)
        return f"agent-{len(self.agents)}"

    async def agent_wait(self, agent_id: str, *, timeout_s: float) -> WaitResult:
        assert timeout_s == 10
        return WaitResult(self.wait_outcome, None, None if self.wait_outcome is WaitOutcome.IDLE else "fake wait failure")

    async def agent_status(self, agent_id: str) -> AgentStatus:
        return AgentStatus.IDLE


class FakeMainSync:
    def __init__(self, *, failure: bool = False) -> None:
        self.failure = failure

    async def align(self, *, repo_path: str) -> str:
        assert repo_path == "/fixed-main"
        if self.failure:
            raise MainSyncFailure("dirty fixed-main")
        return "aligned-sha"


class FakeCommitReader:
    async def read(self, *, repo_path: str) -> str:
        return "candidate-sha" if repo_path != "/deploy" else "baseline-sha"


class FakeE2EReader:
    def __init__(self, summaries: list[RoundSummary]) -> None:
        self.summaries = summaries
        self.calls = 0

    async def read(self, **kwargs: Any) -> E2ERound:
        index = min(self.calls, len(self.summaries) - 1)
        self.calls += 1
        return E2ERound(self.summaries[index], before="before.png", after="after.png")


class FakeRenderer:
    async def render(self, **kwargs: Any) -> RenderedEvidence:
        return RenderedEvidence("explain.png", "evidence.png")


class FakeDeploy:
    async def deploy(self, **kwargs: Any) -> str:
        assert kwargs["commit"] == "candidate-sha"
        return "active"


@dataclass
class FakeEvents:
    events: list[tuple[str, str, object]]

    def __init__(self) -> None:
        self.events = []

    async def candidate_ready(self, run_id: str, payload: CandidateReadyPayload) -> None:
        self.events.append(("candidate_ready", run_id, payload))

    async def main_sync_failed(self, run_id: str, payload: MainSyncFailedPayload) -> None:
        self.events.append(("main_sync_failed", run_id, payload))

    async def e2e_not_converging(self, run_id: str, payload: E2ENotConvergingPayload) -> None:
        self.events.append(("e2e_not_converging", run_id, payload))

    async def deploy_result(self, run_id: str, payload: DeployResultPayload) -> None:
        self.events.append(("deploy_result", run_id, payload))


def _conductor(
    repo: FakeRepo,
    daemon: FakeDaemon,
    events: FakeEvents,
    *,
    main_sync: FakeMainSync | None = None,
    summaries: list[RoundSummary] | None = None,
    deploy: FakeDeploy | None = None,
) -> UpgradeConductor:
    return UpgradeConductor(
        daemon=daemon,
        repo=repo,  # type: ignore[arg-type]
        event_sink=events,
        fixed_main="/fixed-main",
        deploy_target="/deploy",
        provider="provider/test",
        agent_timeout_s=10,
        main_sync=main_sync or FakeMainSync(),
        commit_reader=FakeCommitReader(),
        e2e_reader=FakeE2EReader(summaries or [RoundSummary.from_values((), (), 0)]),
        renderer=FakeRenderer(),
        deploy_runner=deploy or FakeDeploy(),
        paseo_link=lambda workspace_id: (
            "paseo://fixed-main" if workspace_id is None else f"paseo://{workspace_id}"
        ),
    )


async def _wait_for_event(events: FakeEvents, event_name: str) -> None:
    for _ in range(100):
        if any(name == event_name for name, _run_id, _payload in events.events):
            return
        await asyncio.sleep(0)
    raise AssertionError(f"event not observed: {event_name}")


def test_happy_path_state_sequence_and_boundary_events() -> None:
    async def scenario() -> None:
        repo = FakeRepo()
        daemon = FakeDaemon()
        events = FakeEvents()
        conductor = _conductor(repo, daemon, events)

        await conductor.start(prompt="fix command", chat_key="chat", user_key="user")
        await _wait_for_event(events, "candidate_ready")
        await conductor.approve(run_id=RUN_ID, commit="candidate-sha")
        await conductor.wait_for_run(RUN_ID)

        assert list(conductor.state_history[RUN_ID]) == [
            RunState.ACCEPTED,
            RunState.MAIN_SYNC,
            RunState.WORKTREE,
            RunState.IMPLEMENTATION,
            RunState.READ_COMMIT,
            RunState.TEST,
            RunState.E2E,
            RunState.RENDER,
            RunState.CANDIDATE_READY,
            RunState.AWAITING_APPROVAL,
            RunState.DEPLOYING,
            RunState.ACTIVE,
        ]
        assert [name for name, _run_id, _payload in events.events] == [
            "candidate_ready",
            "deploy_result",
        ]
        candidate = events.events[0][2]
        assert isinstance(candidate, CandidateReadyPayload)
        assert candidate.commit == "candidate-sha"
        deployed = events.events[1][2]
        assert isinstance(deployed, DeployResultPayload)
        assert deployed.outcome == "active"
        assert deployed.paseo_link == "paseo://workspace-test"
        assert daemon.archived == ["workspace-test"]
        assert {agent["cwd"] for agent in daemon.agents} == {"/worktree-test"}

    asyncio.run(scenario())


def test_main_sync_failure_emits_only_boundary_event() -> None:
    async def scenario() -> None:
        repo = FakeRepo()
        daemon = FakeDaemon()
        events = FakeEvents()
        conductor = _conductor(repo, daemon, events, main_sync=FakeMainSync(failure=True))

        await conductor.start(prompt="fix command", chat_key="chat", user_key="user")
        await conductor.wait_for_run(RUN_ID)

        assert list(conductor.state_history[RUN_ID]) == [
            RunState.ACCEPTED,
            RunState.MAIN_SYNC,
            RunState.MAIN_SYNC_FAILED,
        ]
        assert [name for name, _run_id, _payload in events.events] == ["main_sync_failed"]
        payload = events.events[0][2]
        assert isinstance(payload, MainSyncFailedPayload)
        assert payload.reason == "dirty fixed-main"
        assert payload.paseo_link == "paseo://fixed-main"
        assert daemon.agents == []
        assert daemon.archived == []

    asyncio.run(scenario())


def test_five_non_converging_rounds_emit_escalation() -> None:
    async def scenario() -> None:
        repo = FakeRepo()
        daemon = FakeDaemon()
        events = FakeEvents()
        plateau = RoundSummary.from_values({"same"}, {"same-error"}, 1)
        e2e = FakeE2EReader([plateau])
        conductor = UpgradeConductor(
            daemon=daemon,
            repo=repo,  # type: ignore[arg-type]
            event_sink=events,
            fixed_main="/fixed-main",
            deploy_target="/deploy",
            provider="provider/test",
            agent_timeout_s=10,
            main_sync=FakeMainSync(),
            commit_reader=FakeCommitReader(),
            e2e_reader=e2e,
            renderer=FakeRenderer(),
            deploy_runner=FakeDeploy(),
            paseo_link=lambda workspace_id: (
                "paseo://fixed-main" if workspace_id is None else f"paseo://{workspace_id}"
            ),
        )
        await conductor.start(prompt="fix command", chat_key="chat", user_key="user")
        await conductor.wait_for_run(RUN_ID)

        assert e2e.calls == 5
        assert [name for name, _run_id, _payload in events.events] == ["e2e_not_converging"]
        payload = events.events[0][2]
        assert isinstance(payload, E2ENotConvergingPayload)
        assert len(payload.round_summaries) == 5
        assert payload.paseo_link == "paseo://workspace-test"
        assert list(conductor.state_history[RUN_ID]) == [
            RunState.ACCEPTED,
            RunState.MAIN_SYNC,
            RunState.WORKTREE,
            *([RunState.IMPLEMENTATION, RunState.READ_COMMIT, RunState.TEST, RunState.E2E] * 5),
            RunState.FAILED,
        ]
        assert daemon.archived == []

    asyncio.run(scenario())
