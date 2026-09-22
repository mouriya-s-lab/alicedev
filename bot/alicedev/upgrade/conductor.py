"""In-process driver for the ``/升级bot`` lifecycle.

The conductor is deliberately boring orchestration: it owns the run state,
asks the daemon for one session at a time, reads typed artifacts from the
worktree/e2e adapter, and emits only the four §15.4 boundary events.  It never
speaks MCP and it never interprets model prose as control data.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from alicedev.paseo.control import DaemonControl, WaitOutcome, WaitResult, WorktreeRef
from alicedev.store.upgrade_runs_repo import UpgradeRunRecord, UpgradeRunStatus, UpgradeRunsRepo

from .convergence import (
    NON_CONVERGING_LIMIT,
    ConvergenceDecision,
    RoundSummary,
    assess,
)
from .state import InvalidTransition, RunState, transition

_LOG = logging.getLogger("alicedev.upgrade.conductor")


class ConductorError(RuntimeError):
    """An expected conductor or adapter failure."""


class MainSyncFailure(ConductorError):
    """The fixed-main alignment shim rejected the checkout."""


class UpgradeEventSink(Protocol):
    """The only conductor-to-message-layer boundary.

    Every method receives the run id separately from its exact §15.4 payload.
    Implementations may send text/images, enqueue an internal callback, or do
    both, but the conductor does not know the transport.
    """

    async def candidate_ready(self, run_id: str, payload: "CandidateReadyPayload") -> None:
        ...

    async def main_sync_failed(self, run_id: str, payload: "MainSyncFailedPayload") -> None:
        ...

    async def e2e_not_converging(self, run_id: str, payload: "E2ENotConvergingPayload") -> None:
        ...

    async def deploy_result(self, run_id: str, payload: "DeployResultPayload") -> None:
        ...


@dataclass(frozen=True)
class CandidateReadyPayload:
    explain_img: str | None = None
    evidence_img: str | None = None
    commit: str = ""

    def __post_init__(self) -> None:
        if not self.commit.strip():
            raise ValueError("candidate commit must not be empty")

@dataclass(frozen=True)
class MainSyncFailedPayload:
    reason: str
    paseo_link: str


@dataclass(frozen=True)
class E2ENotConvergingPayload:
    round_summaries: tuple[RoundSummary, ...]
    paseo_link: str


class DeployOutcome(str, Enum):
    """Allowed terminal deployment outcomes."""

    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"


@dataclass(frozen=True)
class DeployResultPayload:
    outcome: str
    paseo_link: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in {
            DeployOutcome.ACTIVE,
            DeployOutcome.ROLLED_BACK,
            DeployOutcome.ROLLBACK_FAILED,
        }:
            raise ValueError(f"invalid deploy outcome: {self.outcome!r}")


@dataclass(frozen=True)
class RenderedEvidence:
    """References delivered by the rendering seam."""

    explain_img: str | None
    evidence_img: str | None


@dataclass(frozen=True)
class E2ERound:
    """Typed output consumed from one daemon/e2e artifact round."""

    summary: RoundSummary
    before: Mapping[str, object] | str | Path | None = None
    after: Mapping[str, object] | str | Path | None = None
    output_ref: str | Path | None = None


class Clock(Protocol):
    """Clock seam used by adapters and deterministic tests."""

    def monotonic(self) -> float:
        ...

    async def sleep(self, seconds: float) -> None:
        ...


class MainSync(Protocol):
    async def align(self, *, repo_path: str) -> str:
        """Align fixed-main and return its resulting base revision."""


class CommitReader(Protocol):
    async def read(self, *, repo_path: str) -> str:
        """Read an exact worktree/deployment HEAD revision."""


class E2EReader(Protocol):
    async def read(
        self,
        *,
        run_id: str,
        workspace_id: str,
        cwd: str,
        baseline_commit: str,
        candidate_commit: str,
        round_index: int,
    ) -> E2ERound:
        """Read the daemon-organized typed artifact for one E2E round."""


class UpgradeRenderer(Protocol):
    async def render(
        self,
        *,
        run_id: str,
        prompt: str,
        baseline_commit: str,
        candidate_commit: str,
        round_result: E2ERound,
    ) -> RenderedEvidence:
        """Render product explanation and before/after evidence images."""


class DeployRunner(Protocol):
    async def deploy(
        self,
        *,
        run_id: str,
        commit: str,
        target: str,
        paseo_link: str,
    ) -> str:
        """Call deployrun and return one allowed terminal outcome."""


class SystemClock:
    def monotonic(self) -> float:
        return asyncio.get_running_loop().time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _run_process(argv: Sequence[str]) -> tuple[int, str, str]:
    """Run a real shim without a shell or model-facing output parsing."""

    try:
        process = await asyncio.create_subprocess_exec(
            *[str(part) for part in argv],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise ConductorError(f"cannot execute {argv[0]}: {exc}") from exc
    stdout, stderr = await process.communicate()
    return (
        process.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


class ToolsMainSync:
    """Async adapter for the repository's fail-closed ``tools/mainsync``."""

    def __init__(self, executable: str | Path) -> None:
        self._executable = str(executable)

    async def align(self, *, repo_path: str) -> str:
        code, stdout, stderr = await _run_process(
            [self._executable, "align", "--repo", repo_path]
        )
        if code != 0:
            detail = (stderr or stdout).strip() or f"exit status {code}"
            raise MainSyncFailure(detail)
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise MainSyncFailure("mainsync returned no aligned revision")
        return lines[-1].removeprefix("aligned ").strip()


class GitCommitReader:
    """Read a revision through ``git`` without touching the checkout."""

    async def read(self, *, repo_path: str) -> str:
        code, stdout, stderr = await _run_process(
            ["git", "-C", repo_path, "rev-parse", "--verify", "HEAD"]
        )
        if code != 0:
            detail = (stderr or stdout).strip() or f"exit status {code}"
            raise ConductorError(f"cannot read git HEAD for {repo_path}: {detail}")
        revision = stdout.strip().splitlines()
        if not revision or not revision[-1].strip():
            raise ConductorError(f"git HEAD is empty for {repo_path}")
        return revision[-1].strip()


class ToolsDeployRunner:
    """Call ``tools/deployrun`` while keeping event emission in this conductor."""

    def __init__(
        self,
        executable: str | Path,
        *,
        target: str,
        botctl: str | Path | None = None,
    ) -> None:
        self._executable = str(executable)
        self._target = target
        # deployrun's normal callback is intentionally suppressed: the typed
        # event sink below is the sole boundary owner for this in-process path.
        self._botctl = str(botctl) if botctl is not None else shutil.which("true")

    async def deploy(
        self,
        *,
        run_id: str,
        commit: str,
        target: str,
        paseo_link: str,
    ) -> str:
        argv = [
            self._executable,
            "--run-id",
            run_id,
            "--commit",
            commit,
            "--target",
            target or self._target,
        ]
        if paseo_link:
            argv.extend(["--paseo-link", paseo_link])
        if self._botctl:
            argv.extend(["--botctl", self._botctl])
        code, stdout, stderr = await _run_process(argv)
        raw = stdout.strip().splitlines()
        if not raw:
            detail = (stderr or stdout).strip() or f"exit status {code}"
            raise ConductorError(f"deployrun returned no result: {detail}")
        try:
            result = json.loads(raw[-1])
        except json.JSONDecodeError as exc:
            raise ConductorError(f"deployrun returned invalid JSON: {raw[-1]!r}") from exc
        if not isinstance(result, dict) or not isinstance(result.get("outcome"), str):
            raise ConductorError(f"deployrun result has no outcome: {result!r}")
        outcome = result["outcome"]
        if outcome not in {
            DeployOutcome.ACTIVE,
            DeployOutcome.ROLLED_BACK,
            DeployOutcome.ROLLBACK_FAILED,
        }:
            raise ConductorError(f"deployrun returned unknown outcome: {outcome!r}")
        # active is zero; rolled_back/rollback_failed intentionally return one
        # from deployrun, but both are valid typed outcomes.
        del code, stderr
        return outcome


@dataclass
class _Run:
    run_id: str
    prompt: str
    chat_key: str
    user_key: str
    workspace_ref: str
    fixed_main: str
    workspace_id: str | None = None
    workspace_cwd: str | None = None
    baseline_commit: str | None = None
    candidate_commit: str | None = None
    current_state: RunState = RunState.ACCEPTED
    approval: str | None = None
    approval_ready: asyncio.Event | None = None
    rounds: list[RoundSummary] | None = None
    last_round: E2ERound | None = None


class UpgradeConductor:
    """Drive one or more upgrade runs inside the AstrBot process."""

    def __init__(
        self,
        *,
        daemon: DaemonControl,
        repo: UpgradeRunsRepo,
        event_sink: UpgradeEventSink,
        fixed_main: str = "/workspace/alicedev",
        deploy_target: str = "/srv/alicedev/bot",
        provider: str = "omp-alicedev",
        agent_timeout_s: float = 3600.0,
        main_sync: MainSync | None = None,
        commit_reader: CommitReader | None = None,
        e2e_reader: E2EReader | None = None,
        renderer: UpgradeRenderer | None = None,
        deploy_runner: DeployRunner | None = None,
        clock: Clock | None = None,
        paseo_link: Callable[[str | None], str] | None = None,
        mainsync_executable: str | Path | None = None,
        deployrun_executable: str | Path | None = None,
        botctl_executable: str | Path | None = None,
    ) -> None:
        self._daemon = daemon
        self._repo = repo
        self._event_sink = event_sink
        self._fixed_main = fixed_main
        self._deploy_target = deploy_target
        self._provider = provider
        self._agent_timeout_s = agent_timeout_s
        tools_dir = Path(__file__).resolve().parents[3] / "tools"
        self._main_sync = main_sync or ToolsMainSync(
            mainsync_executable or tools_dir / "mainsync"
        )
        self._commit_reader = commit_reader or GitCommitReader()
        self._e2e_reader = e2e_reader
        self._renderer = renderer
        self._deploy_runner = deploy_runner or ToolsDeployRunner(
            deployrun_executable or tools_dir / "deployrun",
            target=deploy_target,
            botctl=botctl_executable,
        )
        self._clock = clock or SystemClock()
        self._paseo_link = paseo_link or (lambda workspace_id: "")
        self._runs: dict[str, _Run] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._history: dict[str, list[RunState]] = {}
        self._lock = asyncio.Lock()

    @property
    def state_history(self) -> Mapping[str, tuple[RunState, ...]]:
        return {run_id: tuple(states) for run_id, states in self._history.items()}

    async def start(self, *, prompt: str, chat_key: str, user_key: str) -> None:
        """Schedule the conductor task for the accepted row created by the command."""

        record = await self._accepted_record(chat_key=chat_key, user_key=user_key)
        run = _Run(
            run_id=record.run_id,
            prompt=prompt,
            chat_key=chat_key,
            user_key=user_key,
            workspace_ref=record.workspace_ref,
            fixed_main=self._fixed_main,
            approval_ready=asyncio.Event(),
            rounds=[],
        )
        async with self._lock:
            if run.run_id in self._tasks and not self._tasks[run.run_id].done():
                raise ConductorError(f"upgrade run already active: {run.run_id}")
            self._runs[run.run_id] = run
            self._history[run.run_id] = [RunState.ACCEPTED]
            task = asyncio.create_task(self._drive(run), name=f"alicedev-upgrade-{run.run_id}")
            self._tasks[run.run_id] = task
        # Let the task perform its first await so a caller can immediately see
        # daemon/main-sync effects while still returning the command response.
        await _maybe_await(self._clock.sleep(0))

    async def wait_for_run(self, run_id: str) -> None:
        """Await a scheduled run; useful to lifecycle shutdown and deterministic tests."""

        task = self._tasks.get(run_id)
        if task is None:
            raise ConductorError(f"unknown upgrade run: {run_id}")
        await task

    async def approve(self, *, run_id: str, commit: str) -> None:
        """Approve exactly the candidate recorded for a waiting run."""

        run = self._runs.get(run_id)
        record = await self._repo.get(run_id)
        if record is None:
            raise ConductorError(f"unknown upgrade run: {run_id}")
        expected = record.candidate_commit
        if not expected or expected != commit:
            raise ConductorError("candidate commit does not match the recorded candidate")
        if record.status not in {
            UpgradeRunStatus.CANDIDATE_READY,
            UpgradeRunStatus.AWAITING_APPROVAL,
        }:
            raise ConductorError(f"run {run_id} is not awaiting approval: {record.status.value}")
        if run is None or run.approval_ready is None:
            raise ConductorError(f"run {run_id} has no live conductor task")
        if run.current_state is RunState.CANDIDATE_READY:
            self._set_state(run, RunState.AWAITING_APPROVAL)
        if run.current_state is not RunState.AWAITING_APPROVAL:
            raise ConductorError(f"run {run_id} is not awaiting approval")
        if not await self._repo.mark_deploying(run_id):
            raise ConductorError(f"run {run_id} could not enter deploying")
        self._set_state(run, RunState.DEPLOYING)
        run.approval = commit
        run.approval_ready.set()

    async def reject(self, *, run_id: str) -> None:
        """Reject a waiting run and let its task unwind without deployment."""

        run = self._runs.get(run_id)
        if run is not None and run.approval_ready is not None:
            record = await self._repo.get(run_id)
            if record is None:
                raise ConductorError(f"unknown upgrade run: {run_id}")
            if record.status in {
                UpgradeRunStatus.CANDIDATE_READY,
                UpgradeRunStatus.AWAITING_APPROVAL,
            }:
                if run.current_state is RunState.CANDIDATE_READY:
                    self._set_state(run, RunState.AWAITING_APPROVAL)
                if run.current_state is RunState.AWAITING_APPROVAL:
                    if not await self._repo.reject(run_id):
                        raise ConductorError(f"run {run_id} could not be rejected")
                    self._set_state(run, RunState.FAILED)
                    run.approval = "rejected"
                    run.approval_ready.set()
                    return
        if not await self._repo.reject(run_id):
            raise ConductorError(f"run {run_id} could not be rejected")

    async def _accepted_record(self, *, chat_key: str, user_key: str) -> UpgradeRunRecord:
        record = await self._repo.latest_for_chat(chat_key)
        if record is None or record.user_key != user_key or record.status is not UpgradeRunStatus.ACCEPTED:
            raise ConductorError("no accepted upgrade run is available for this chat")
        return record

    def _set_state(self, run: _Run, next_state: RunState) -> None:
        if run.current_state is next_state:
            return
        try:
            run.current_state = transition(run.current_state, next_state)
        except InvalidTransition as exc:
            raise ConductorError(str(exc)) from exc
        history = self._history[run.run_id]
        history.append(next_state)

    @staticmethod
    def _worktree_parts(worktree: WorktreeRef, fixed_main: str) -> tuple[str, str]:
        """Validate the daemon-owned workspace identity and exact worktree cwd."""

        if not isinstance(worktree, WorktreeRef):
            raise ConductorError("daemon returned an invalid worktree reference")
        workspace_id = worktree.workspace_id
        cwd = worktree.cwd
        if not workspace_id.strip():
            raise ConductorError("daemon returned an empty workspace id")
        if not cwd.strip():
            raise ConductorError("daemon returned an empty worktree cwd")
        if Path(cwd).expanduser().resolve() == Path(fixed_main).expanduser().resolve():
            raise ConductorError("daemon worktree cwd must not be fixed-main")
        return workspace_id.strip(), cwd.strip()

    async def _drive(self, run: _Run) -> None:
        try:
            await self._drive_checked(run)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("upgrade run failed: %s", run.run_id)
            await self._mark_failed(run)

    async def _drive_checked(self, run: _Run) -> None:
        await _maybe_await(self._daemon.connect())
        self._set_state(run, RunState.MAIN_SYNC)
        try:
            aligned = await self._main_sync.align(repo_path=run.fixed_main)
        except MainSyncFailure as exc:
            await self._main_sync_failed(run, str(exc))
            return
        except Exception as exc:
            await self._main_sync_failed(run, str(exc) or "fixed-main alignment failed")
            return
        if not isinstance(aligned, str) or not aligned.strip():
            await self._main_sync_failed(run, "fixed-main alignment returned no revision")
            return
        base_ref = aligned.strip()

        self._set_state(run, RunState.WORKTREE)
        worktree = await self._daemon.worktree_create(
            repo_path=run.fixed_main,
            base_ref=base_ref,
            name=run.workspace_ref,
        )
        run.workspace_id, run.workspace_cwd = self._worktree_parts(worktree, run.fixed_main)
        run.baseline_commit = await self._commit_reader.read(repo_path=self._deploy_target)
        if not run.baseline_commit.strip():
            raise ConductorError("deployment baseline commit is empty")

        candidate, round_result = await self._converge(run)
        if candidate is None or round_result is None:
            return
        run.candidate_commit = candidate

        self._set_state(run, RunState.RENDER)
        if self._renderer is None:
            raise ConductorError("upgrade evidence renderer is not configured")
        rendered = await _maybe_await(
            self._renderer.render(
                run_id=run.run_id,
                prompt=run.prompt,
                baseline_commit=run.baseline_commit,
                candidate_commit=candidate,
                round_result=round_result,
            )
        )
        if not isinstance(rendered, RenderedEvidence):
            raise ConductorError(f"renderer returned an invalid result: {rendered!r}")
        self._set_state(run, RunState.CANDIDATE_READY)
        if not await self._repo.mark_candidate_ready(
            run.run_id,
            commit=candidate,
            evidence_refs={
                "explain_img": rendered.explain_img or "",
                "evidence_img": rendered.evidence_img or "",
            },
        ):
            raise ConductorError(f"run {run.run_id} disappeared before candidate_ready")
        # Persist the approval state before yielding to the event sink.  The
        # message layer may receive the candidate event and approve immediately;
        # publishing first would race that approval against this transition.
        self._set_state(run, RunState.AWAITING_APPROVAL)
        if not await self._repo.mark_awaiting_approval(run.run_id):
            raise ConductorError(f"run {run.run_id} could not enter awaiting_approval")
        await _maybe_await(
            self._event_sink.candidate_ready(
                run.run_id,
                CandidateReadyPayload(rendered.explain_img, rendered.evidence_img, candidate),
            )
        )
        assert run.approval_ready is not None
        await run.approval_ready.wait()
        if run.approval != candidate:
            return
        if run.current_state is not RunState.DEPLOYING:
            raise ConductorError(f"run {run.run_id} approval did not enter deploying")
        paseo_link = self._link(run.workspace_id)
        if self._deploy_runner is None:
            raise ConductorError("deployment runner is not configured")
        outcome = await _maybe_await(
            self._deploy_runner.deploy(
                run_id=run.run_id,
                commit=candidate,
                target=self._deploy_target,
                paseo_link=paseo_link,
            )
        )
        if outcome not in {
            DeployOutcome.ACTIVE,
            DeployOutcome.ROLLED_BACK,
            DeployOutcome.ROLLBACK_FAILED,
        }:
            raise ConductorError(f"unknown deploy outcome: {outcome!r}")
        self._set_state(
            run,
            {
                DeployOutcome.ACTIVE: RunState.ACTIVE,
                DeployOutcome.ROLLED_BACK: RunState.ROLLED_BACK,
                DeployOutcome.ROLLBACK_FAILED: RunState.ROLLBACK_FAILED,
            }[outcome],
        )
        if not await self._repo.mark_deploy_result(run.run_id, outcome):
            raise ConductorError(f"run {run.run_id} disappeared during deploy")
        await _maybe_await(
            self._event_sink.deploy_result(
                run.run_id,
                DeployResultPayload(outcome, paseo_link or None),
            )
        )
        if outcome != DeployOutcome.ROLLBACK_FAILED:
            await self._daemon.worktree_archive(run.workspace_id)

    async def _converge(self, run: _Run) -> tuple[str | None, E2ERound | None]:
        assert run.workspace_id is not None
        assert run.workspace_cwd is not None
        assert run.baseline_commit is not None
        assert run.rounds is not None
        previous: RoundSummary | None = None
        non_converging = 0
        round_index = 0
        while True:
            round_index += 1
            self._set_state(run, RunState.IMPLEMENTATION)
            impl_id = await self._daemon.agent_create(
                workspace_id=run.workspace_id,
                provider=self._provider,
                cwd=run.workspace_cwd,
                title=f"alicedev upgrade implementation {run.run_id}",
                initial_prompt=self._implementation_prompt(run),
                labels={"alicedev_run": run.run_id, "alicedev_phase": "implementation"},
            )
            impl_wait = await self._daemon.agent_wait(impl_id, timeout_s=self._agent_timeout_s)
            if impl_wait.outcome is not WaitOutcome.IDLE:
                summary = self._failure_summary("impl", impl_wait)
                if await self._record_round(run, previous, summary, run.rounds, non_converging):
                    return None, None
                previous, non_converging = summary, self._next_streak(previous, summary, non_converging)
                continue

            self._set_state(run, RunState.READ_COMMIT)
            candidate = await self._commit_reader.read(repo_path=run.workspace_cwd)
            if not candidate.strip():
                summary = RoundSummary.from_values(("impl",), ("empty_commit",), 1)
                previous, non_converging = summary, self._next_streak(previous, summary, non_converging)
                run.rounds.append(summary)
                if non_converging >= NON_CONVERGING_LIMIT:
                    await self._e2e_not_converging(run)
                    return None, None
                continue
            run.candidate_commit = candidate.strip()

            self._set_state(run, RunState.TEST)
            test_id = await self._daemon.agent_create(
                workspace_id=run.workspace_id,
                provider=self._provider,
                cwd=run.workspace_cwd,
                title=f"alicedev upgrade tests {run.run_id} round {round_index}",
                initial_prompt=self._test_prompt(run, run.candidate_commit),
                labels={"alicedev_run": run.run_id, "alicedev_phase": "test"},
            )
            test_wait = await self._daemon.agent_wait(test_id, timeout_s=self._agent_timeout_s)
            if test_wait.outcome is not WaitOutcome.IDLE:
                summary = self._failure_summary("test", test_wait)
                run.rounds.append(summary)
                previous, non_converging = summary, self._next_streak(previous, summary, non_converging)
                if non_converging >= NON_CONVERGING_LIMIT:
                    await self._e2e_not_converging(run)
                    return None, None
                continue

            self._set_state(run, RunState.E2E)
            e2e_id = await self._daemon.agent_create(
                workspace_id=run.workspace_id,
                provider=self._provider,
                cwd=run.workspace_cwd,
                title=f"alicedev upgrade e2e {run.run_id} round {round_index}",
                initial_prompt=self._e2e_prompt(run, run.candidate_commit, round_index),
                labels={"alicedev_run": run.run_id, "alicedev_phase": "e2e"},
            )
            e2e_wait = await self._daemon.agent_wait(e2e_id, timeout_s=self._agent_timeout_s)
            if e2e_wait.outcome is not WaitOutcome.IDLE:
                summary = self._failure_summary("e2e", e2e_wait)
                run.rounds.append(summary)
                previous, non_converging = summary, self._next_streak(previous, summary, non_converging)
                if non_converging >= NON_CONVERGING_LIMIT:
                    await self._e2e_not_converging(run)
                    return None, None
                continue
            if self._e2e_reader is None:
                raise ConductorError("E2E artifact reader is not configured")
            result = await _maybe_await(
                self._e2e_reader.read(
                    run_id=run.run_id,
                    workspace_id=run.workspace_id,
                    cwd=run.workspace_cwd,
                    baseline_commit=run.baseline_commit,
                    candidate_commit=run.candidate_commit,
                    round_index=round_index,
                )
            )
            if not isinstance(result, E2ERound):
                raise ConductorError(f"E2E reader returned an invalid result: {result!r}")
            run.last_round = result
            summary = result.summary
            run.rounds.append(summary)
            decision = assess(previous, summary)
            if decision.decision is ConvergenceDecision.GREEN:
                return run.candidate_commit, result
            non_converging = (
                non_converging + 1
                if decision.decision is ConvergenceDecision.NON_CONVERGING
                else 0
            )
            if non_converging >= NON_CONVERGING_LIMIT:
                await self._e2e_not_converging(run)
                return None, None
            previous = summary

    @staticmethod
    def _next_streak(
        previous: RoundSummary | None,
        current: RoundSummary,
        streak: int,
    ) -> int:
        decision = assess(previous, current)
        if decision.decision is ConvergenceDecision.NON_CONVERGING:
            return streak + 1
        return 0

    async def _record_round(
        self,
        run: _Run,
        previous: RoundSummary | None,
        summary: RoundSummary,
        rounds: list[RoundSummary],
        streak: int,
    ) -> bool:
        rounds.append(summary)
        next_streak = self._next_streak(previous, summary, streak)
        if next_streak >= NON_CONVERGING_LIMIT:
            await self._e2e_not_converging(run)
            return True
        return False

    @staticmethod
    def _failure_summary(phase: str, wait: WaitResult) -> RoundSummary:
        error = wait.error.strip() if isinstance(wait.error, str) and wait.error.strip() else f"{phase}_{wait.outcome.value}"
        return RoundSummary.from_values((phase,), (error,), 1)

    async def _main_sync_failed(self, run: _Run, reason: str) -> None:
        self._set_state(run, RunState.MAIN_SYNC_FAILED)
        await self._repo.mark_main_sync_failed(run.run_id)
        await _maybe_await(
            self._event_sink.main_sync_failed(
                run.run_id,
                MainSyncFailedPayload(reason or "fixed-main alignment failed", self._link(None)),
            )
        )

    async def _e2e_not_converging(self, run: _Run) -> None:
        self._set_state(run, RunState.FAILED)
        await self._repo.mark_failed(run.run_id)
        await _maybe_await(
            self._event_sink.e2e_not_converging(
                run.run_id,
                E2ENotConvergingPayload(tuple(run.rounds or ()), self._link(run.workspace_id)),
            )
        )

    async def _mark_failed(self, run: _Run) -> None:
        if run.current_state in {
            RunState.ACTIVE,
            RunState.ROLLED_BACK,
            RunState.ROLLBACK_FAILED,
            RunState.FAILED,
            RunState.MAIN_SYNC_FAILED,
        }:
            return
        try:
            self._set_state(run, RunState.FAILED)
        except ConductorError:
            _LOG.exception("could not enter failed state: %s", run.run_id)
        try:
            await self._repo.mark_failed(run.run_id)
        except Exception:
            _LOG.exception("could not persist failed state: %s", run.run_id)

    def _link(self, workspace_id: str | None) -> str:
        try:
            return str(self._paseo_link(workspace_id) or "")
        except Exception:
            _LOG.exception("failed to build Paseo link")
            return ""

    @staticmethod
    def _implementation_prompt(run: _Run) -> str:
        return (
            "Implement the requested alicedev bot change in this worktree.\n"
            "Use the repository's existing conventions, run focused checks, and commit the change.\n"
            f"Upgrade request: {run.prompt}"
        )

    @staticmethod
    def _test_prompt(run: _Run, candidate: str) -> str:
        return (
            "Review the implementation in this worktree and run the relevant tests.\n"
            "Fix test failures in the worktree, then leave the candidate commit ready for E2E.\n"
            f"Candidate commit: {candidate}\nOriginal request: {run.prompt}"
        )

    @staticmethod
    def _e2e_prompt(run: _Run, candidate: str, round_index: int) -> str:
        return (
            "Run the frozen before/after E2E scenario through the repository's documented shims.\n"
            "Record the typed E2E artifact for this round; do not report control data as model prose.\n"
            f"Round: {round_index}\nCandidate commit: {candidate}\n"
            f"Original request: {run.prompt}"
        )


# Names used by composition roots and migration callers.
Conductor = UpgradeConductor
RealUpgradeConductor = UpgradeConductor

__all__ = [
    "CandidateReadyPayload",
    "Clock",
    "Conductor",
    "ConductorError",
    "DeployOutcome",
    "DeployResultPayload",
    "DeployRunner",
    "E2ENotConvergingPayload",
    "E2ERound",
    "E2EReader",
    "GitCommitReader",
    "MainSync",
    "MainSyncFailure",
    "MainSyncFailedPayload",
    "RenderedEvidence",
    "RoundSummary",
    "SystemClock",
    "ToolsDeployRunner",
    "ToolsMainSync",
    "UpgradeConductor",
    "UpgradeEventSink",
    "UpgradeRenderer",
]
