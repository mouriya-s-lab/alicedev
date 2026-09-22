"""One-time migration of a v2 database to schema v3 (plan design 6).

v2 keyed everything on ``session_ref`` (``s_…``) with one paseo agent per
session. v3 splits user-visible sessions (per-chat ``no``) from ``agents``.

Mapping (all in one transaction; legacy rows are kept as ``v2_*`` copies):

* ``sessions`` → ``sessions`` (numbered per chat by created_at) + ``agents``
  (new ``a_`` ref, ``agent_id`` kept, ``legacy_ref`` = old ``s_`` ref so replies
  from pre-migration agents still resolve). Status → state: active/closed →
  ``discussing`` (the single conversational state of the chat scenarios),
  creating/failed → ``failed``, archived → ``archived``.
* ``requirements`` → merged into ``requirement`` sessions (text becomes the
  session input); requirements without a session become archived sessions.
* ``chat_current_sessions`` → re-pointed to ``session_id`` (only live sessions).
* ``messages`` → ``agent_ref`` via the legacy map.
* ``reply_deliveries`` → ``outbox`` rows in state ``sent`` (never resent).
* ``outbound`` / ``reports`` / ``tokens_issued`` → ``session_id``.
* ``upgrade_runs`` and ``seq_requirements`` are dropped.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

MIGRATION = "v3_sessions_agents"

_LEGACY_TABLES = (
    "sessions",
    "chat_current_sessions",
    "messages",
    "reply_deliveries",
    "outbound",
    "requirements",
    "reports",
    "tokens_issued",
    "upgrade_runs",
)

_CONVERSATIONAL_STATE = "discussing"
_TERMINAL_STATES = ("failed", "archived", "main_sync_failed")
_NAME_LIMIT = 60
_B32 = "abcdefghijklmnopqrstuvwxyz234567"


def _new_agent_ref() -> str:
    return "a_" + "".join(secrets.choice(_B32) for _ in range(10))


def bounded_name(prefix: str, text: str, limit: int = _NAME_LIMIT) -> str:
    """``prefix + text`` squashed to one line and bounded to ``limit`` chars."""
    body = " ".join(str(text).split())
    name = f"{prefix}{body}".strip()
    return name if len(name) <= limit else name[: limit - 1] + "…"


def _table_columns(conn: Any, table: str) -> list[str]:
    try:
        return [str(r[1]) for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]
    except Exception:  # noqa: BLE001 - table missing
        return []


def needs_migration(conn: Any) -> bool:
    return "session_ref" in _table_columns(conn, "sessions")


def migrate_if_needed(conn: Any, schema_sql: str) -> bool:
    """Migrate a v2 database in place. Returns True if a migration ran."""
    if not needs_migration(conn):
        return False
    conn.execute("BEGIN")
    try:
        present = [t for t in _LEGACY_TABLES if _table_columns(conn, t)]
        for table in present:
            conn.execute(f"DROP TABLE IF EXISTS v2_{table}")
            conn.execute(f"CREATE TABLE v2_{table} AS SELECT * FROM {table}")
            conn.execute(f"DROP TABLE {table}")
        conn.execute("DROP SEQUENCE IF EXISTS seq_requirements")
        conn.execute(schema_sql)
        _copy(conn, set(present))
        conn.execute(
            "INSERT INTO schema_migrations (migration) VALUES (?) ON CONFLICT DO NOTHING",
            (MIGRATION,),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return True


def _rows(conn: Any, table: str, present: set[str]) -> list[dict[str, Any]]:
    if table not in present:
        return []
    cur = conn.execute(f"SELECT * FROM v2_{table}")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _state_for(status: str) -> str:
    match status:
        case "active" | "closed":
            return _CONVERSATIONAL_STATE
        case "archived":
            return "archived"
        case _:
            return "failed"


def _agent_status_for(status: str) -> str:
    match status:
        case "active" | "closed" | "archived" | "failed":
            return status
        case _:
            return "failed"


def _ts_key(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value or "")


def _copy(conn: Any, present: set[str]) -> None:
    legacy_sessions = _rows(conn, "sessions", present)
    requirements = _rows(conn, "requirements", present)
    req_by_session: dict[str, dict[str, Any]] = {}
    orphan_reqs: list[dict[str, Any]] = []
    for req in sorted(requirements, key=lambda r: int(r["id"])):
        ref = req.get("session_ref")
        if ref and str(ref) not in req_by_session:
            req_by_session[str(ref)] = req
        elif not ref:
            orphan_reqs.append(req)

    # Build the v3 session list (legacy sessions + orphan requirements), then
    # number per chat by creation time.
    entries: list[dict[str, Any]] = []
    for s in legacy_sessions:
        ref = str(s["session_ref"])
        req = req_by_session.get(ref)
        text = str(req["text"]) if req else ""
        name = s.get("name") or ""
        if not str(name).strip():
            name = bounded_name("需求 · ", text) if req else bounded_name("历史会话 · ", ref)
        entries.append(
            {
                "legacy": s,
                "chat_key": str(s["chat_key"]),
                "scenario": str(s["template"]),
                "name": str(name),
                "created_by": str(s["created_by"]),
                "input": {"text": text} if req else {},
                "state": _state_for(str(s["status"])),
                "created_at": s["created_at"],
                "updated_at": s.get("last_activity_at") or s["created_at"],
                "sort": (_ts_key(s["created_at"]), ref),
            }
        )
    for req in orphan_reqs:
        entries.append(
            {
                "legacy": None,
                "chat_key": str(req["chat_key"]),
                "scenario": "requirement",
                "name": bounded_name("需求 · ", str(req["text"])),
                "created_by": str(req["author_key"]),
                "input": {"text": str(req["text"])},
                "state": "archived",
                "created_at": req["created_at"],
                "updated_at": req["created_at"],
                "sort": (_ts_key(req["created_at"]), f"req{int(req['id']):012d}"),
            }
        )

    entries.sort(key=lambda e: (e["chat_key"], e["sort"]))
    ref_to_session: dict[str, int] = {}
    ref_to_agent: dict[str, str] = {}
    live_session: dict[int, bool] = {}
    per_chat_no: dict[str, int] = {}
    next_id = 0
    for e in entries:
        next_id += 1
        chat = e["chat_key"]
        per_chat_no[chat] = per_chat_no.get(chat, 0) + 1
        session_id = next_id
        conn.execute(
            "INSERT INTO sessions (session_id, chat_key, no, scenario, name, created_by, "
            "input, state, data, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                session_id, chat, per_chat_no[chat], e["scenario"], e["name"],
                e["created_by"], json.dumps(e["input"], ensure_ascii=False), e["state"],
                "{}", e["created_at"], e["updated_at"],
            ),
        )
        live_session[session_id] = e["state"] not in _TERMINAL_STATES
        s = e["legacy"]
        if s is None:
            continue
        ref = str(s["session_ref"])
        ref_to_session[ref] = session_id
        if s.get("agent_id"):
            agent_ref = _new_agent_ref()
            ref_to_agent[ref] = agent_ref
            provider = str(s.get("provider") or "")
            model = s.get("model")
            if model:
                provider = f"{provider}/{model}" if provider else str(model)
            conn.execute(
                "INSERT INTO agents (agent_ref, session_id, state, provider, agent_id, "
                "workspace_id, server_id, status, legacy_ref, created_at, last_activity_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    agent_ref, session_id,
                    _CONVERSATIONAL_STATE if e["state"] not in _TERMINAL_STATES else e["state"],
                    provider, s.get("agent_id"), s.get("workspace_id"), s.get("server_id"),
                    _agent_status_for(str(s["status"])), ref,
                    s["created_at"], s.get("last_activity_at") or s["created_at"],
                ),
            )

    for row in _rows(conn, "chat_current_sessions", present):
        sid = ref_to_session.get(str(row["current_session_ref"]))
        if sid is not None and live_session.get(sid):
            conn.execute(
                "INSERT INTO chat_current_sessions (chat_key, session_id, updated_at) "
                "VALUES (?,?,?) ON CONFLICT DO NOTHING",
                (row["chat_key"], sid, row["updated_at"]),
            )

    for row in _rows(conn, "messages", present):
        ref = str(row["session_ref"])
        conn.execute(
            "INSERT INTO messages (msg_ref, agent_ref, chat_key, platform_message_id, "
            "sender_key, text, session_id, created_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT DO NOTHING",
            (
                row["msg_ref"], ref_to_agent.get(ref), row["chat_key"],
                row["platform_message_id"], row["sender_key"], row["text"],
                ref_to_session.get(ref), row["created_at"],
            ),
        )

    session_chat: dict[str, str] = {str(s["session_ref"]): str(s["chat_key"]) for s in legacy_sessions}
    seq = 0
    for row in sorted(
        _rows(conn, "reply_deliveries", present), key=lambda r: _ts_key(r["created_at"])
    ):
        ref = str(row["session_ref"])
        chat = session_chat.get(ref)
        if chat is None:
            continue
        seq += 1
        state = "failed" if str(row["state"]) == "failed" else "sent"
        conn.execute(
            "INSERT INTO outbox (reply_id, seq, chat_key, session_id, agent_ref, msgs, payload, "
            "payload_sha256, state, attempts, platform_message_ids, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["reply_id"], seq, chat, ref_to_session.get(ref), ref_to_agent.get(ref),
                _json_text(row["msgs"], "[]"), json.dumps({"kind": "migrated_v2"}),
                row["payload_sha256"], state, 0, _json_text(row["platform_message_ids"], "[]"),
                row["created_at"], row["updated_at"],
            ),
        )

    for row in _rows(conn, "outbound", present):
        conn.execute(
            "INSERT INTO outbound (platform_message_id, chat_key, session_id, created_at) "
            "VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
            (row["platform_message_id"], row["chat_key"],
             ref_to_session.get(str(row["session_ref"])), row["created_at"]),
        )
    for row in _rows(conn, "reports", present):
        conn.execute(
            "INSERT INTO reports (report_id, session_id, source_path, published_path, created_at) "
            "VALUES (?,?,?,?,?)",
            (row["report_id"], ref_to_session.get(str(row["session_ref"] or "")),
             row["source_path"], row["published_path"], row["created_at"]),
        )
    for row in _rows(conn, "tokens_issued", present):
        conn.execute(
            "INSERT INTO tokens_issued (token_id, session_id, user_key, issued_by, target, "
            "issued_at, expires_at) VALUES (?,?,?,?,?,?,?)",
            (row["token_id"], ref_to_session.get(str(row["session_ref"] or "")),
             row["user_key"], row["issued_by"], row["target"], row["issued_at"],
             row["expires_at"]),
        )


def _json_text(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
