"""MCP transport for ``PaseoControl`` — ``POST /mcp/agents`` (ARCHITECTURE §4).

The daemon exposes a *stateless* Streamable-HTTP MCP endpoint whose tools are
``create_agent``, ``send_agent_prompt``, ``get_agent_status``, ``list_agents``,
``kill_agent`` (= resumable close), and ``archive_agent``. Auth is
``Authorization: Bearer $PASEO_PASSWORD``. We issue raw JSON-RPC; the client
performs the ``initialize`` handshake lazily and retries a call once if the
server reports it is not initialized.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from alicedev.paseo.control import (
    LABEL_KEY,
    AgentHandle,
    AgentStatus,
    PaseoControl,
    PaseoError,
)

_LOG = logging.getLogger("alicedev.paseo.mcp")

_PROTOCOL_VERSION = "2025-06-18"


def _map_status(raw: str | None) -> AgentStatus:
    match (raw or "").lower():
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


class MCPPaseoControl(PaseoControl):
    def __init__(self, base_url: str, password: str, *, timeout_s: float = 60.0) -> None:
        # base_url is the daemon origin, e.g. http://paseo:6767
        self._base_url = base_url.rstrip("/")
        self._endpoint = self._base_url + "/mcp/agents"
        self._password = password
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: aiohttp.ClientSession | None = None
        self._server_id: str | None = None
        self._rpc_id = 0

    async def connect(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _server_id_from_status(self) -> str | None:
        if self._server_id is not None:
            return self._server_id
        await self.connect()
        assert self._session is not None
        try:
            async with self._session.get(
                self._base_url + "/api/status",
                headers={"Authorization": f"Bearer {self._password}"},
            ) as resp:
                if resp.status >= 400:
                    return None
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError):
            return None
        raw = payload.get("serverId") if isinstance(payload, dict) else None
        if isinstance(raw, str) and raw:
            self._server_id = raw
        return self._server_id

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        assert self._session is not None
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._password}",
        }
        try:
            async with self._session.post(self._endpoint, json=payload, headers=headers) as resp:
                if resp.status == 401:
                    raise PaseoError("paseo MCP unauthorized (check PASEO_PASSWORD)")
                body = await resp.text()
                if resp.status >= 400:
                    raise PaseoError(f"paseo MCP HTTP {resp.status}: {body[:500]}")
                ctype = resp.headers.get("Content-Type", "")
                if "text/event-stream" in ctype:
                    return _parse_sse(body)
                if not body.strip():
                    return None
                return json.loads(body)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise PaseoError(f"paseo MCP unreachable: {exc}") from exc

    async def _initialize(self) -> None:
        await self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "alicedev", "version": "0.1.0"},
                },
            }
        )

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self.connect()
        request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        result = await self._post(request)
        if _is_not_initialized(result):
            await self._initialize()
            result = await self._post(request)
        return _structured(name, result)

    # --- PaseoControl operations ---------------------------------------

    async def create(
        self, *, session_ref: str, provider: str, model: str, thinking: str,
        cwd: str, title: str, initial_prompt: str,
    ) -> AgentHandle:
        if not model:
            raise PaseoError("paseo MCP create_agent requires a model (provider/model form)")
        args: dict[str, Any] = {
            "title": title,
            "provider": f"{provider}/{model}",
            "initialPrompt": initial_prompt,
            "background": True,
            "labels": {LABEL_KEY: session_ref},
            "relationship": {"kind": "detached"},
            "workspace": {"kind": "create", "source": {"kind": "directory", "path": cwd}},
        }
        if thinking:
            args["settings"] = {"thinkingOptionId": thinking}
        out = await self._call_tool("create_agent", args)
        agent_id = out.get("agentId")
        if not agent_id:
            raise PaseoError(f"create_agent returned no agentId: {out}")
        server_id = str(out["serverId"]) if out.get("serverId") else None
        if server_id is None:
            server_id = await self._server_id_from_status()
        return AgentHandle(
            agent_id=str(agent_id),
            workspace_id=(str(out["workspaceId"]) if out.get("workspaceId") else None),
            server_id=server_id,
        )

    async def find_by_label(self, session_ref: str) -> AgentHandle | None:
        out = await self._call_tool(
            "list_agents", {"includeArchived": False, "limit": 200, "sinceHours": 720}
        )
        for agent in out.get("agents", []):
            labels = agent.get("labels") or {}
            if labels.get(LABEL_KEY) == session_ref:
                server_id = str(agent["serverId"]) if agent.get("serverId") else None
                if server_id is None:
                    server_id = await self._server_id_from_status()
                return AgentHandle(
                    agent_id=str(agent["id"]),
                    workspace_id=(str(agent["workspaceId"]) if agent.get("workspaceId") else None),
                    server_id=server_id,
                )
        return None

    async def send(self, agent_id: str, text: str) -> None:
        await self._call_tool(
            "send_agent_prompt", {"agentId": agent_id, "prompt": text, "background": True}
        )

    async def status(self, agent_id: str) -> AgentStatus:
        try:
            out = await self._call_tool("get_agent_status", {"agentId": agent_id})
        except PaseoError as exc:
            if "not found" in str(exc).lower():
                return AgentStatus.CLOSED
            raise
        return _map_status(out.get("status"))

    async def close(self, agent_id: str) -> None:
        await self._call_tool("kill_agent", {"agentId": agent_id})

    async def archive(self, agent_id: str) -> None:
        await self._call_tool("archive_agent", {"agentId": agent_id})


def _parse_sse(body: str) -> dict[str, Any] | None:
    """Extract the last JSON ``data:`` event from an SSE response body."""
    last: dict[str, Any] | None = None
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            chunk = line[len("data:"):].strip()
            if chunk and chunk != "[DONE]":
                try:
                    last = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
    return last


def _is_not_initialized(result: dict[str, Any] | None) -> bool:
    if not result:
        return False
    err = result.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message", "")).lower()
        return "not initialized" in msg or "initialize" in msg
    return False


def _structured(tool: str, result: dict[str, Any] | None) -> dict[str, Any]:
    if result is None:
        raise PaseoError(f"{tool}: empty MCP response")
    if "error" in result and result["error"]:
        raise PaseoError(f"{tool}: {result['error']}")
    payload = result.get("result")
    if payload is None:
        raise PaseoError(f"{tool}: MCP response missing result: {result}")
    if payload.get("isError"):
        text = _tool_text(payload)
        raise PaseoError(f"{tool} tool error: {text}")
    structured = payload.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    # Fall back to parsing the text content block as JSON.
    text = _tool_text(payload)
    if text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    return {}


def _tool_text(payload: dict[str, Any]) -> str:
    parts = []
    for block in payload.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)
