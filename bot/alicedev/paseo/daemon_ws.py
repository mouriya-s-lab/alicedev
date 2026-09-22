"""WebSocket control-plane transport for the Paseo daemon.

The conductor uses the daemon's authenticated ``/ws`` session protocol directly
rather than the stateless MCP endpoint.  Each request is correlated by the
request ID carried in the inner session message; one reader task owns all
incoming frames so concurrent conductor operations cannot consume one another's
responses.
"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from alicedev.paseo.control import (
    AgentStatus,
    DaemonControl,
    PaseoError,
    WaitOutcome,
    WaitResult,
    WorktreeRef,
)


class WSDaemonControl(DaemonControl):
    """Authenticated ``/ws`` client for the ``/升级bot`` conductor."""

    def __init__(
        self,
        base_url: str,
        password: str,
        *,
        timeout_s: float = 60.0,
        client_id: str | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("paseo base URL is required")
        if timeout_s <= 0 or not math.isfinite(timeout_s):
            raise ValueError("paseo timeout must be a finite positive number")

        self._ws_url = _websocket_url(base_url)
        self._password = password
        self._timeout_s = timeout_s
        self._client_id = client_id or f"alicedev-{uuid.uuid4().hex}"

        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._connect_lock = asyncio.Lock()
        self._server_id: str | None = None
        self._request_counter = 0
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    @property
    def server_id(self) -> str | None:
        """The server ID received during the most recent hello handshake."""

        return self._server_id

    async def connect(self) -> None:
        """Open the authenticated socket and complete the daemon hello handshake."""

        if self._transport_ready():
            return

        async with self._connect_lock:
            if self._transport_ready():
                return

            # A previous reader can have observed a close between the fast path
            # and this lock.  Fail anything still waiting before replacing the
            # transport, then dispose the stale reader/socket as one generation.
            old_ws = self._ws
            old_reader = self._reader_task
            self._ws = None
            self._reader_task = None
            self._server_id = None
            self._fail_pending(PaseoError("paseo WebSocket reconnecting"))
            if old_reader is not None and old_reader is not asyncio.current_task():
                old_reader.cancel()
                await asyncio.gather(old_reader, return_exceptions=True)
            if old_ws is not None and not old_ws.closed:
                await old_ws.close()

            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout_s)
                )

            ws: aiohttp.ClientWebSocketResponse | None = None
            try:
                assert self._session is not None
                ws = await self._session.ws_connect(
                    self._ws_url,
                    headers={"Authorization": f"Bearer {self._password}"},
                    protocols=[f"paseo.bearer.{self._password}"],
                )
                self._ws = ws
                await ws.send_json(
                    {
                        "type": "hello",
                        "clientId": self._client_id,
                        "clientType": "cli",
                        "protocolVersion": 1,
                        "appVersion": "alicedev",
                    }
                )
                server_id = await asyncio.wait_for(
                    self._receive_server_info(ws), timeout=self._timeout_s
                )
                self._server_id = server_id
                self._reader_task = asyncio.create_task(self._reader_loop(ws))
            except PaseoError:
                if self._ws is ws:
                    self._ws = None
                if ws is not None and not ws.closed:
                    await ws.close()
                raise
            except (aiohttp.ClientError, OSError, TimeoutError, asyncio.CancelledError) as exc:
                if self._ws is ws:
                    self._ws = None
                if ws is not None and not ws.closed:
                    await ws.close()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if isinstance(exc, TimeoutError):
                    raise PaseoError("paseo WebSocket hello timed out") from exc
                raise PaseoError(f"paseo WebSocket connection failed: {exc}") from exc

    async def aclose(self) -> None:
        """Close the socket, fail outstanding calls, and release the HTTP session."""

        async with self._connect_lock:
            ws = self._ws
            reader = self._reader_task
            self._ws = None
            self._reader_task = None
            self._server_id = None
            self._fail_pending(PaseoError("paseo WebSocket closed"))

            if reader is not None and reader is not asyncio.current_task():
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            if ws is not None and not ws.closed:
                await ws.close()

            if self._session is not None and not self._session.closed:
                await self._session.close()
            self._session = None

    # --- DaemonControl operations -------------------------------------

    async def worktree_create(
        self, *, repo_path: str, base_ref: str, name: str
    ) -> WorktreeRef:
        response = await self._request(
            "workspace.create.request",
            {
                "source": {
                    "kind": "worktree",
                    "cwd": repo_path,
                    "action": "branch-off",
                    "baseBranch": base_ref,
                    "branchName": name,
                    "worktreeSlug": name,
                }
            },
            expected=("workspace.create.response",),
        )
        payload = _payload(response)
        _raise_payload_error(payload, "workspace create")
        workspace = payload.get("workspace")
        if not isinstance(workspace, Mapping):
            raise PaseoError("workspace create returned no workspace")
        workspace_id = workspace.get("id")
        if not isinstance(workspace_id, str) or not workspace_id:
            raise PaseoError("workspace create returned no workspace ID")
        workspace_cwd = workspace.get("workspaceDirectory")
        if not isinstance(workspace_cwd, str) or not workspace_cwd:
            workspace_cwd = workspace.get("cwd")
        if not isinstance(workspace_cwd, str) or not workspace_cwd:
            raise PaseoError("workspace create returned no workspace path")
        return WorktreeRef(workspace_id=workspace_id, cwd=workspace_cwd)

    async def worktree_archive(self, workspace_id: str) -> None:
        response = await self._request(
            "archive_workspace_request",
            {"workspaceId": workspace_id},
            expected=("archive_workspace_response",),
        )
        _raise_payload_error(_payload(response), "workspace archive")

    async def agent_create(
        self,
        *,
        workspace_id: str,
        provider: str,
        cwd: str,
        title: str,
        initial_prompt: str,
        labels: dict[str, str],
    ) -> str:
        response = await self._request(
            "create_agent_request",
            {
                "config": {"provider": provider, "cwd": cwd, "title": title},
                "workspaceId": workspace_id,
                "initialPrompt": initial_prompt,
                "labels": labels,
            },
            expected=("status",),
        )
        payload = _payload(response)
        status = payload.get("status")
        if status == "agent_create_failed":
            detail = payload.get("error")
            raise PaseoError(
                f"paseo agent create failed: {detail}" if detail else "paseo agent create failed"
            )
        if status != "agent_created":
            raise PaseoError(f"unexpected agent create status: {status!r}")
        agent_id = payload.get("agentId")
        if not isinstance(agent_id, str) or not agent_id:
            agent = payload.get("agent")
            agent_id = agent.get("id") if isinstance(agent, Mapping) else None
        if not isinstance(agent_id, str) or not agent_id:
            raise PaseoError("agent create returned no agent ID")
        return agent_id

    async def agent_wait(self, agent_id: str, *, timeout_s: float) -> WaitResult:
        request: dict[str, Any] = {"agentId": agent_id}
        if timeout_s > 0 and math.isfinite(timeout_s):
            request["timeoutMs"] = max(1, int(timeout_s * 1000))
        response = await self._request(
            "wait_for_finish_request",
            request,
            expected=("wait_for_finish_response",),
            timeout_s=max(self._timeout_s, timeout_s + 1.0) if timeout_s > 0 else None,
        )
        payload = _payload(response)
        raw_outcome = payload.get("status")
        try:
            outcome = WaitOutcome(raw_outcome)
        except ValueError as exc:
            raise PaseoError(f"unknown wait outcome: {raw_outcome!r}") from exc
        return WaitResult(
            outcome=outcome,
            last_message=_optional_string(payload.get("lastMessage")),
            error=_optional_string(payload.get("error")),
        )

    async def agent_status(self, agent_id: str) -> AgentStatus:
        response = await self._request(
            "fetch_agents_request",
            {"scope": "active"},
            expected=("fetch_agents_response",),
        )
        payload = _payload(response)
        _raise_payload_error(payload, "fetch agents")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise PaseoError("fetch agents returned no entries")
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            agent = entry.get("agent", entry)
            if not isinstance(agent, Mapping) or agent.get("id") != agent_id:
                continue
            return _map_status(agent.get("status"))
        # Active-directory lookup cannot return archived/closed agents.  Match
        # the existing MCP transport's not-found semantics for the conductor.
        return AgentStatus.CLOSED

    # --- Transport internals ------------------------------------------

    def _transport_ready(self) -> bool:
        return (
            self._ws is not None
            and not self._ws.closed
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    def _next_request_id(self) -> str:
        self._request_counter += 1
        return f"alicedev-{self._request_counter}-{uuid.uuid4().hex}"

    async def _request(
        self,
        message_type: str,
        fields: Mapping[str, Any],
        *,
        expected: Sequence[str],
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        await self.connect()
        ws = self._ws
        if ws is None or ws.closed:
            raise PaseoError("paseo WebSocket is not connected")

        request_id = self._next_request_id()
        message: dict[str, Any] = {"type": message_type, **fields, "requestId": request_id}
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        try:
            await ws.send_json({"type": "session", "message": message})
            response = await asyncio.wait_for(
                future, timeout=timeout_s if timeout_s is not None else self._timeout_s
            )
        except asyncio.TimeoutError as exc:
            raise PaseoError(f"paseo request {message_type} timed out") from exc
        except PaseoError:
            raise
        except (aiohttp.ClientError, OSError, RuntimeError) as exc:
            raise PaseoError(f"paseo request {message_type} failed: {exc}") from exc
        finally:
            self._pending.pop(request_id, None)

        if response.get("type") not in expected:
            raise PaseoError(
                f"unexpected paseo response for {message_type}: {response.get('type')!r}"
            )
        return response

    async def _receive_server_info(self, ws: aiohttp.ClientWebSocketResponse) -> str:
        while True:
            message = await ws.receive()
            if message.type == aiohttp.WSMsgType.TEXT:
                try:
                    raw = json.loads(message.data)
                except (TypeError, ValueError) as exc:
                    raise PaseoError("paseo WebSocket sent invalid JSON during hello") from exc
                inner = _inner_message(raw)
                if _message_type(inner) in {"error", "rpc_error"}:
                    raise _error_from_message(inner)
                payload = _payload(inner)
                if payload.get("status") == "server_info":
                    server_id = payload.get("serverId")
                    if isinstance(server_id, str) and server_id:
                        return server_id
                    raise PaseoError("paseo server_info has no server ID")
                if inner.get("type") == "server_info":
                    server_id = inner.get("serverId")
                    if isinstance(server_id, str) and server_id:
                        return server_id
                    raise PaseoError("paseo server_info has no server ID")
                continue
            if message.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                raise PaseoError("paseo WebSocket closed during hello")

    async def _reader_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        reason = PaseoError("paseo WebSocket disconnected")
        try:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        raw = json.loads(message.data)
                    except (TypeError, ValueError):
                        reason = PaseoError("paseo WebSocket sent invalid JSON")
                        break
                    self._dispatch(raw)
                elif message.type == aiohttp.WSMsgType.ERROR:
                    reason = PaseoError("paseo WebSocket transport error")
                    break
                elif message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                    break
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, OSError, RuntimeError) as exc:
            reason = PaseoError(f"paseo WebSocket disconnected: {exc}")
        finally:
            if self._ws is ws:
                self._ws = None
                self._reader_task = None
                self._server_id = None
                self._fail_pending(reason)

    def _dispatch(self, raw: object) -> None:
        if not isinstance(raw, Mapping):
            return
        message = _inner_message(raw)
        request_id = _request_id(message)
        if _message_type(message) in {"error", "rpc_error"}:
            error = _error_from_message(message)
            if request_id is not None:
                future = self._pending.get(request_id)
                if future is not None and not future.done():
                    future.set_exception(error)
                    return
            if len(self._pending) == 1:
                future = next(iter(self._pending.values()))
                if not future.done():
                    future.set_exception(error)
            return
        if request_id is None:
            return
        future = self._pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(dict(message))

    def _fail_pending(self, error: PaseoError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)


def _websocket_url(base_url: str) -> str:
    parsed = urlsplit(base_url.rstrip("/"))
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    path = parsed.path.rstrip("/")
    if not path.endswith("/ws"):
        path += "/ws"
    return urlunsplit((scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def _inner_message(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    if raw.get("type") == "session" and isinstance(raw.get("message"), Mapping):
        return raw["message"]
    return raw


def _message_type(message: Mapping[str, Any]) -> str | None:
    raw = message.get("type")
    return raw if isinstance(raw, str) else None


def _payload(message: Mapping[str, Any]) -> dict[str, Any]:
    payload = message.get("payload")
    if isinstance(payload, Mapping):
        return dict(payload)
    return dict(message)


def _request_id(message: Mapping[str, Any]) -> str | None:
    direct = message.get("requestId")
    if isinstance(direct, str):
        return direct
    payload = message.get("payload")
    if isinstance(payload, Mapping):
        nested = payload.get("requestId")
        if isinstance(nested, str):
            return nested
    return None


def _error_from_message(message: Mapping[str, Any]) -> PaseoError:
    payload = message.get("payload")
    candidates: list[object] = []
    if isinstance(payload, Mapping):
        candidates.extend((payload.get("error"), payload.get("message"), payload.get("code")))
    elif isinstance(payload, str):
        candidates.append(payload)
    candidates.extend((message.get("error"), message.get("message"), message.get("code")))
    detail = next((value for value in candidates if isinstance(value, str) and value), "unknown error")
    return PaseoError(f"paseo RPC error: {detail}")


def _raise_payload_error(payload: Mapping[str, Any], operation: str) -> None:
    error = payload.get("error")
    if isinstance(error, str) and error:
        raise PaseoError(f"paseo {operation} failed: {error}")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _map_status(raw: object) -> AgentStatus:
    match str(raw or "").lower():
        case "idle":
            return AgentStatus.IDLE
        case "running" | "initializing":
            return AgentStatus.RUNNING
        case "permission":
            return AgentStatus.PERMISSION
        case "closed":
            return AgentStatus.CLOSED
        case "error":
            return AgentStatus.ERROR
        case _:
            return AgentStatus.UNKNOWN
