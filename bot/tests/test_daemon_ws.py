from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
import pytest

# The plugin package is intentionally a top-level ``alicedev`` package when
# AstrBot loads it.  Mirror bot/main.py's path setup for this repo-local test.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alicedev.paseo.control import AgentStatus, PaseoError, WaitOutcome, WorktreeRef
from alicedev.paseo.daemon_ws import WSDaemonControl


PASSWORD = "fake-password"


class FakeDaemon:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.rpc_error_next = False
        self.auth_header: str | None = None
        self.protocol_header: str | None = None

    async def handler(self, request: web.Request) -> web.StreamResponse:
        self.auth_header = request.headers.get("Authorization")
        self.protocol_header = request.headers.get("Sec-WebSocket-Protocol")
        ws = web.WebSocketResponse(protocols=(f"paseo.bearer.{PASSWORD}",))
        await ws.prepare(request)
        async for incoming in ws:
            if incoming.type != aiohttp.WSMsgType.TEXT:
                continue
            frame = json.loads(incoming.data)
            self.frames.append(frame)
            if frame.get("type") == "hello":
                await ws.send_json(
                    {
                        "type": "status",
                        "payload": {"status": "server_info", "serverId": "fake-server"},
                    }
                )
                continue

            message = frame["message"]
            request_id = message["requestId"]
            if self.rpc_error_next:
                self.rpc_error_next = False
                await self._send(
                    ws,
                    "rpc_error",
                    {"requestId": request_id, "error": "permission denied", "code": "forbidden"},
                )
                continue
            message_type = message["type"]
            if message_type == "workspace.create.request":
                await self._send(
                    ws,
                    "workspace.create.response",
                    {
                        "requestId": request_id,
                        "workspace": {
                            "id": "workspace-1",
                            "workspaceDirectory": "/worktrees/upgrade-run",
                        },
                        "setupTerminalId": None,
                        "error": None,
                    },
                )
            elif message_type == "archive_workspace_request":
                await self._send(
                    ws,
                    "archive_workspace_response",
                    {
                        "requestId": request_id,
                        "workspaceId": message["workspaceId"],
                        "archivedAt": "2026-09-23T00:00:00Z",
                        "error": None,
                    },
                )
            elif message_type == "create_agent_request":
                if message["config"]["title"] == "fail":
                    await self._send(
                        ws,
                        "status",
                        {
                            "status": "agent_create_failed",
                            "requestId": request_id,
                            "error": "provider unavailable",
                            "errorCode": "provider_unavailable",
                        },
                    )
                else:
                    await self._send(
                        ws,
                        "status",
                        {
                            "status": "agent_created",
                            "requestId": request_id,
                            "agentId": "agent-1",
                            "agent": {"id": "agent-1", "status": "idle"},
                        },
                    )
            elif message_type == "wait_for_finish_request":
                await self._send(
                    ws,
                    "wait_for_finish_response",
                    {
                        "requestId": request_id,
                        "status": "timeout",
                        "final": None,
                        "error": "wait expired",
                        "lastMessage": "still working",
                    },
                )
            elif message_type == "fetch_agents_request":
                await self._send(
                    ws,
                    "fetch_agents_response",
                    {
                        "requestId": request_id,
                        "entries": [
                            {"agent": {"id": "agent-1", "status": "running"}},
                        ],
                        "pageInfo": {"nextCursor": None, "prevCursor": None, "hasMore": False},
                    },
                )
            else:
                raise AssertionError(f"fake daemon received unexpected message: {message_type}")
        return ws

    async def _send(
        self, ws: web.WebSocketResponse, message_type: str, payload: dict[str, Any]
    ) -> None:
        await ws.send_json({"type": "session", "message": {"type": message_type, "payload": payload}})

    def session_frames(self) -> list[dict[str, Any]]:
        return [frame["message"] for frame in self.frames if frame.get("type") == "session"]


async def _start_server(fake: FakeDaemon) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_get("/ws", fake.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    address = site._server.sockets[0].getsockname()  # type: ignore[union-attr]
    return runner, f"http://127.0.0.1:{address[1]}"


def test_daemon_ws_operations_and_wire_frames() -> None:
    asyncio.run(_test_daemon_ws_operations_and_wire_frames())


async def _test_daemon_ws_operations_and_wire_frames() -> None:
    fake = FakeDaemon()
    runner, base_url = await _start_server(fake)
    control = WSDaemonControl(base_url, PASSWORD, timeout_s=2.0, client_id="test-client")
    try:
        await control.connect()
        assert control.server_id == "fake-server"
        assert fake.auth_header == f"Bearer {PASSWORD}"
        assert fake.protocol_header == f"paseo.bearer.{PASSWORD}"

        worktree = await control.worktree_create(
            repo_path="/workspace/alicedev", base_ref="main", name="upgrade-run"
        )
        assert worktree == WorktreeRef(workspace_id="workspace-1", cwd="/worktrees/upgrade-run")
        await control.worktree_archive(worktree.workspace_id)
        agent_id = await control.agent_create(
            workspace_id=worktree.workspace_id,
            provider="omp",
            cwd=worktree.cwd,
            title="Implement feature",
            initial_prompt="Implement the requested feature.",
            labels={"alicedev_run": "run-1"},
        )
        assert agent_id == "agent-1"
        waited = await control.agent_wait("agent-1", timeout_s=0.25)
        assert waited.outcome is WaitOutcome.TIMEOUT
        assert waited.last_message == "still working"
        assert waited.error == "wait expired"
        assert await control.agent_status("agent-1") is AgentStatus.RUNNING

        frames = fake.session_frames()
        workspace = frames[0]
        assert workspace["type"] == "workspace.create.request"
        assert workspace["source"] == {
            "kind": "worktree",
            "cwd": "/workspace/alicedev",
            "action": "branch-off",
            "baseBranch": "main",
            "branchName": "upgrade-run",
            "worktreeSlug": "upgrade-run",
        }
        archive = frames[1]
        assert archive["type"] == "archive_workspace_request"
        assert archive["workspaceId"] == "workspace-1"
        create = frames[2]
        assert create["type"] == "create_agent_request"
        assert create["config"] == {
            "provider": "omp",
            "cwd": "/worktrees/upgrade-run",
            "title": "Implement feature",
        }
        assert create["workspaceId"] == "workspace-1"
        assert create["initialPrompt"] == "Implement the requested feature."
        assert create["labels"] == {"alicedev_run": "run-1"}
        wait = frames[3]
        assert wait["type"] == "wait_for_finish_request"
        assert wait["agentId"] == "agent-1"
        assert wait["timeoutMs"] == 250
        fetch = frames[4]
        assert fetch["type"] == "fetch_agents_request"
        assert fetch["scope"] == "active"
        for frame in frames:
            assert isinstance(frame["requestId"], str) and frame["requestId"]
    finally:
        await control.aclose()
        await runner.cleanup()


def test_daemon_ws_agent_errors_are_surfaceable() -> None:
    asyncio.run(_test_daemon_ws_agent_errors_are_surfaceable())


async def _test_daemon_ws_agent_errors_are_surfaceable() -> None:
    fake = FakeDaemon()
    runner, base_url = await _start_server(fake)
    control = WSDaemonControl(base_url, PASSWORD, timeout_s=2.0, client_id="error-test")
    try:
        with pytest.raises(PaseoError, match="provider unavailable"):
            await control.agent_create(
                workspace_id="workspace-1",
                provider="omp",
                cwd="/workspace/alicedev",
                title="fail",
                initial_prompt="Please fail.",
                labels={},
            )

        fake.rpc_error_next = True
        with pytest.raises(PaseoError, match="permission denied"):
            await control.agent_status("agent-1")
    finally:
        await control.aclose()
        await runner.cleanup()
