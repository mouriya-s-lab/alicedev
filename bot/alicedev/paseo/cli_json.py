"""Parsing of paseo CLI ``--json`` output (paseo 0.8.0).

All knowledge of the CLI's JSON shapes lives here. Shapes observed on prod
(nekoringo2, paseo 0.8.0, 2026-09-23):

* ``ls --json`` / ``ls --label k=v --json``: list of
  ``{id, shortId, name, provider, thinking, status, cwd, created}``.
* ``inspect <id> --json``: PascalCase object ``{Id, Name, Provider, Model,
  Status, Archived, Cwd, Worktree, ...}``.
* ``run ... --json``: ``{agentId, status, provider, cwd, title}`` (source
  ``cli/dist/commands/agent/run.d.ts``; no workspace id).
* ``workspace create|ls --json``: ``{workspaceId, project, name, isolation, cwd}``
  (single object for create, list for ls).
* ``status --json``: ``{serverId, daemonVersion, ...}``.
* Errors: ``{"error": {"code", "message"}}`` (exit code may be 0 or 1).

Parsing is defensive: unknown extra keys are ignored, missing required keys
raise :class:`CliShapeError`.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from alicedev.paseo.control import AgentStatus, WorkspaceRef


class CliShapeError(ValueError):
    """CLI output did not have the expected shape."""


def loads(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        raise CliShapeError("empty CLI output")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some commands print a log line before the JSON document.
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise CliShapeError(f"not JSON: {text[:200]!r}") from None
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError as exc:
            raise CliShapeError(f"not JSON: {text[:200]!r}") from exc


def error_of(doc: Any) -> str | None:
    """The CLI's structured error message, if the document is an error."""
    if isinstance(doc, Mapping) and isinstance(doc.get("error"), Mapping):
        err = doc["error"]
        return f"{err.get('code', 'ERROR')}: {err.get('message', '')}".strip()
    return None


def _get(doc: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in doc and doc[key] is not None:
            return doc[key]
    return None


def map_status(raw: Any) -> AgentStatus:
    value = str(raw or "").strip().lower()
    match value:
        case "idle" | "created" | "completed" | "ready":
            return AgentStatus.IDLE
        case "running" | "busy" | "working" | "streaming":
            return AgentStatus.RUNNING
        case "permission" | "awaiting_permission" | "waiting_permission":
            return AgentStatus.PERMISSION
        case "closed" | "stopped" | "archived":
            return AgentStatus.CLOSED
        case "error" | "failed" | "timeout":
            return AgentStatus.ERROR
        case _:
            return AgentStatus.UNKNOWN


def run_agent_id(doc: Any) -> str:
    if not isinstance(doc, Mapping):
        raise CliShapeError("run: expected object")
    agent_id = _get(doc, "agentId", "id", "Id")
    if not isinstance(agent_id, str) or not agent_id:
        raise CliShapeError(f"run: no agentId in {doc!r}")
    return agent_id


def ls_agent_ids(doc: Any) -> list[str]:
    if not isinstance(doc, list):
        raise CliShapeError("ls: expected list")
    ids: list[str] = []
    for item in doc:
        if isinstance(item, Mapping):
            agent_id = _get(item, "id", "agentId", "Id")
            if isinstance(agent_id, str) and agent_id:
                ids.append(agent_id)
    return ids


def inspect_status(doc: Any) -> AgentStatus:
    if not isinstance(doc, Mapping):
        raise CliShapeError("inspect: expected object")
    if doc.get("Archived") is True or doc.get("archived") is True:
        return AgentStatus.CLOSED
    status = _get(doc, "Status", "status")
    if status is None:
        raise CliShapeError(f"inspect: no Status in {list(doc)!r}")
    if doc.get("PendingPermissions"):
        return AgentStatus.PERMISSION
    return map_status(status)


def workspace_ref(doc: Any) -> WorkspaceRef:
    item = doc[0] if isinstance(doc, list) and doc else doc
    if not isinstance(item, Mapping):
        raise CliShapeError("workspace: expected object")
    workspace_id = _get(item, "workspaceId", "id", "WorkspaceId")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise CliShapeError(f"workspace: no workspaceId in {item!r}")
    return WorkspaceRef(workspace_id=workspace_id, cwd=str(_get(item, "cwd", "Cwd") or ""))


def server_id(doc: Any) -> str | None:
    if not isinstance(doc, Mapping):
        return None
    value = _get(doc, "serverId", "ServerId")
    return str(value) if value else None
