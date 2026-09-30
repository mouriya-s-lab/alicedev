"""PaseoControl over the paseo CLI (ARCHITECTURE §4).

Every operation is one ``docker exec -u paseo <paseo container> paseo <cmd> … --json``
(the ``paseoctl`` shim contract). CLI and git both run as the daemon's user
``paseo`` so ``~/.paseo`` (including the CLI client identity) and ``/workspace``
keep uid 1000. No shell is involved: argv only.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Sequence

from alicedev.paseo import cli_json
from alicedev.paseo.control import (
    LABEL_KEY,
    AgentHandle,
    AgentStatus,
    GitResult,
    LiveAgent,
    PaseoControl,
    PaseoError,
    WorkspaceRef,
)

_LOG = logging.getLogger("alicedev.paseo.cli")

Runner = Callable[[Sequence[str], float], Awaitable[tuple[int, str, str]]]


async def subprocess_runner(argv: Sequence[str], timeout: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise PaseoError(f"timed out after {timeout}s: {' '.join(argv[:6])} …") from None
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


class CliPaseoControl(PaseoControl):
    def __init__(
        self,
        *,
        container: str = "alicedev-paseo",
        paseo_bin: str = "paseo",
        docker_bin: str = "docker",
        runner: Runner = subprocess_runner,
        timeout_s: float = 120.0,
    ) -> None:
        self._container = container
        self._paseo = paseo_bin
        self._docker = docker_bin
        self._runner = runner
        self._timeout = timeout_s
        self._server_id: str | None = None

    # --- plumbing ------------------------------------------------------------

    def _exec(self, *args: str) -> list[str]:
        # As the daemon's own user: a root exec leaves root-owned files in /home/paseo.
        return [self._docker, "exec", "-u", "paseo", self._container, *args]

    async def _paseo_json(self, *args: str, timeout: float | None = None) -> object:
        argv = self._exec(self._paseo, *args, "--json")
        rc, out, err = await self._runner(argv, timeout or self._timeout)
        try:
            doc = cli_json.loads(out)
        except cli_json.CliShapeError as exc:
            raise PaseoError(f"paseo {args[0]} failed (rc={rc}): {err.strip() or exc}") from exc
        problem = cli_json.error_of(doc)
        if problem:
            raise PaseoError(f"paseo {args[0]}: {problem}")
        if rc != 0:
            raise PaseoError(f"paseo {args[0]} exited {rc}: {err.strip()[:500]}")
        return doc

    # --- agents --------------------------------------------------------------

    async def create(
        self,
        *,
        agent_ref: str,
        provider: str,
        cwd: str,
        title: str,
        initial_prompt: str,
        workspace_id: str,
    ) -> AgentHandle:
        doc = await self._paseo_json(
            "run", initial_prompt, "--background", "--provider", provider, "--cwd", cwd,
            "--title", title, "--label", f"{LABEL_KEY}={agent_ref}",
            "--workspace", workspace_id,
        )
        try:
            agent_id = cli_json.run_agent_id(doc)
        except cli_json.CliShapeError as exc:
            raise PaseoError(str(exc)) from exc
        return AgentHandle(agent_id=agent_id, workspace_id=workspace_id,
                           server_id=await self.server_id())

    async def find_by_label(self, agent_ref: str) -> AgentHandle | None:
        doc = await self._paseo_json("ls", "-a", "-g", "--label", f"{LABEL_KEY}={agent_ref}")
        try:
            ids = cli_json.ls_agent_ids(doc)
        except cli_json.CliShapeError as exc:
            raise PaseoError(str(exc)) from exc
        if not ids:
            return None
        return AgentHandle(agent_id=ids[0], workspace_id=None, server_id=await self.server_id())

    async def send(self, agent_id: str, text: str) -> None:
        # --no-wait: send returns once queued; the default blocks until the turn ends.
        argv = self._exec(self._paseo, "send", "--no-wait", agent_id, "--prompt", text)
        rc, out, err = await self._runner(argv, self._timeout)
        if rc != 0:
            raise PaseoError(f"paseo send exited {rc}: {(err or out).strip()[:500]}")

    async def status(self, agent_id: str) -> AgentStatus:
        doc = await self._paseo_json("inspect", agent_id)
        try:
            return cli_json.inspect_status(doc)
        except cli_json.CliShapeError as exc:
            raise PaseoError(str(exc)) from exc

    async def live_agents(self) -> list[LiveAgent]:
        doc = await self._paseo_json("ls")
        try:
            pairs = cli_json.ls_agents(doc)
        except cli_json.CliShapeError as exc:
            raise PaseoError(str(exc)) from exc
        return [LiveAgent(agent_id=i, status=s) for i, s in pairs if s is not AgentStatus.CLOSED]

    async def park(self, agent_id: str) -> None:
        # Without --force paseo archives only an idle agent (a running one is refused).
        # Archive terminates the agent's omp runtime and keeps the record; `send` later
        # unarchives it and resumes the persisted conversation. `stop` cannot do this:
        # it is a no-op for idle agents.
        argv = self._exec(self._paseo, "archive", agent_id)
        rc, out, err = await self._runner(argv, self._timeout)
        if rc != 0:
            raise PaseoError(f"paseo archive exited {rc}: {(err or out).strip()[:500]}")

    async def archive(self, agent_id: str) -> None:
        # --force: ending a session archives its agents even mid-turn.
        argv = self._exec(self._paseo, "archive", "--force", agent_id)
        rc, out, err = await self._runner(argv, self._timeout)
        if rc != 0:
            raise PaseoError(f"paseo archive exited {rc}: {(err or out).strip()[:500]}")

    # --- workspaces ------------------------------------------------------------

    async def workspace_local(self, path: str, title: str) -> WorkspaceRef:
        doc = await self._paseo_json(
            "workspace", "create", "--isolation", "local", "--path", path, "--title", title
        )
        try:
            return cli_json.workspace_ref(doc)
        except cli_json.CliShapeError as exc:
            raise PaseoError(str(exc)) from exc

    async def worktree_create(self, *, repo: str, base_ref: str, slug: str) -> WorkspaceRef:
        doc = await self._paseo_json(
            "workspace", "create", "--isolation", "worktree", "--path", repo,
            "--mode", "branch-off", "--base", base_ref, "--worktree-slug", slug,
            "--new-branch", f"alicedev/{slug}", "--title", slug,
            timeout=300.0,
        )
        try:
            return cli_json.workspace_ref(doc)
        except cli_json.CliShapeError as exc:
            raise PaseoError(str(exc)) from exc

    async def workspace_archive(self, workspace_id: str) -> None:
        argv = self._exec(self._paseo, "workspace", "archive", workspace_id)
        rc, out, err = await self._runner(argv, self._timeout)
        if rc != 0:
            raise PaseoError(f"paseo workspace archive exited {rc}: {(err or out).strip()[:500]}")

    async def server_id(self) -> str | None:
        if self._server_id is None:
            try:
                self._server_id = cli_json.server_id(await self._paseo_json("status"))
            except PaseoError:
                _LOG.warning("paseo status failed; server id unknown", exc_info=True)
        return self._server_id

    # --- git ---------------------------------------------------------------------

    async def git(self, repo: str, *args: str) -> GitResult:
        argv = self._exec("git", "-C", repo, *args)
        rc, out, err = await self._runner(argv, 300.0)
        return GitResult(returncode=rc, stdout=out, stderr=err)
