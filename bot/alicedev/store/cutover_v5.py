"""Authorized one-time retirement and restoration of original requirement facts.

Called only after the maintenance daemon reset, before admissions. Visible paseo
objects are explicitly archived; other rows rely on the verified runtime reset,
not an inferred storage absence. Failed initialization never resumes old agents.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import duckdb

from alicedev.domain import JsonValue
from alicedev.paseo.control import PaseoControl, PaseoError
from alicedev.store.db import Store
from alicedev.store.requirements_repo import RequirementQuote, image_paths, quote_json

_TARGETS = ("requirement", "steward", "investigate", "github-issue", "github-pr")
_TERMINAL = ("archived", "failed", "main_sync_failed", "done", "rejected")
_MARKER = "v5_requirements_typed_tools"


@dataclass(frozen=True)
class CutoverReport:
    skipped: bool
    retired_sessions: int
    archived_agents: int
    runtime_reset_agents: int
    legacy_requirements: int
    native_requirements: int
    missing_originals: int


@dataclass(frozen=True)
class RetirementAgent:
    ref: str
    agent_id: str | None
    legacy_ref: str | None


class RetirementBasis(Enum):
    PASEO_ARCHIVED = "paseo_archived"
    RUNTIME_RESET = "runtime_reset"


@dataclass(frozen=True)
class RetiredAgent:
    ref: str
    agent_id: str | None
    basis: RetirementBasis


@dataclass(frozen=True)
class NativeRequirement:
    session_id: int
    chat: str
    author: str
    name: str
    text: str
    images: tuple[str, ...]
    quoted: RequirementQuote | None
    created_at: datetime


def _native(row: tuple[int, str, str, str, datetime]) -> NativeRequirement | None:
    session_id, chat, author, raw, created_at = row
    value: JsonValue = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("requirement session input must be an object")
    text = value.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    sender = value.get("sender")
    name = sender.get("name") if isinstance(sender, dict) else None
    if not isinstance(name, str) or not name:
        name = author
    quoted = value.get("quoted")
    quote = None
    if quoted is not None:
        if not isinstance(quoted, dict):
            raise ValueError("requirement input.quoted must be an object")
        quote_name, quote_text = quoted.get("sender"), quoted.get("text")
        if not isinstance(quote_name, str) or not isinstance(quote_text, str):
            raise ValueError("requirement input.quoted must contain sender and text")
        quote = RequirementQuote(None, quote_name, quote_text, image_paths(quoted.get("images")))
    return NativeRequirement(session_id, chat, author, name, text,
                             image_paths(value.get("images")), quote, created_at)


def _commit(conn: duckdb.DuckDBPyConnection, retired_agents: tuple[RetiredAgent, ...]) -> CutoverReport:
    tables = {row[0] for row in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    boundary = 0
    legacy_count = 0
    if "v2_sessions" in tables:
        boundary = conn.execute("SELECT COUNT(*) FROM v2_sessions").fetchone()[0]
    if "v2_requirements" in tables:
        legacy_count = conn.execute("SELECT COUNT(*) FROM v2_requirements").fetchone()[0]
        boundary += conn.execute("SELECT COUNT(*) FROM v2_requirements WHERE session_ref IS NULL OR session_ref = ''").fetchone()[0]
        originals = conn.execute(
            "SELECT id,chat_key,author_key,author_name,text,images,status,created_at FROM v2_requirements ORDER BY id"
        ).fetchall()
        for record_id, chat, author, name, text, images, status, created_at in originals:
            image_paths(json.loads(images) if images is not None else None)
            conn.execute(
                "INSERT INTO requirements (id,chat_key,author_key,author_name,text,images,quoted,status,source_kind,source_ref,created_at) "
                "VALUES (?,?,?,?,?,?,NULL,?,'v2_requirement',?,?)",
                (record_id, chat, author, name, text, images, status, str(record_id), created_at),
            )
    next_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM requirements").fetchone()[0]
    native_count = missing = 0
    rows = conn.execute(
        "SELECT session_id, chat_key, created_by, input, created_at FROM sessions "
        "WHERE scenario = 'requirement' AND session_id > ? ORDER BY session_id", (boundary,)
    ).fetchall()
    for row in rows:
        record = _native(row)
        if record is None:
            missing += 1
            continue
        next_id += 1
        conn.execute(
            "INSERT INTO requirements (id, chat_key, author_key, author_name, text, images, quoted, status, source_kind, source_ref, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 'requirement_session', ?, ?)",
            (next_id, record.chat, record.author, record.name, record.text,
             json.dumps(list(record.images), ensure_ascii=False), json.dumps(quote_json(record.quoted), ensure_ascii=False),
             str(record.session_id), record.created_at),
        )
        native_count += 1
    for agent in retired_agents:
        conn.execute("UPDATE agents SET status = 'archived', agent_id = COALESCE(?, agent_id) WHERE agent_ref = ?",
                     (agent.agent_id, agent.ref))
    marks = ",".join("?" for _ in _TARGETS)
    terminal_marks = ",".join("?" for _ in _TERMINAL)
    retired = conn.execute(
        f"SELECT COUNT(*) FROM sessions WHERE scenario IN ({marks}) AND state NOT IN ({terminal_marks})",
        (*_TARGETS, *_TERMINAL),
    ).fetchone()[0]
    conn.execute(f"DELETE FROM chat_current_sessions WHERE session_id IN (SELECT session_id FROM sessions WHERE scenario IN ({marks}))", _TARGETS)
    conn.execute(
        f"UPDATE sessions SET state = 'archived', updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') "
        f"WHERE scenario IN ({marks}) AND state NOT IN ({terminal_marks})", (*_TARGETS, *_TERMINAL),
    )
    conn.execute("DROP SEQUENCE IF EXISTS seq_requirements")
    conn.execute(f"CREATE SEQUENCE seq_requirements START {next_id + 1}")
    conn.execute("INSERT INTO schema_migrations (migration) VALUES (?)", (_MARKER,))
    conn.execute("INSERT INTO schema_version (version) VALUES (5)")
    archived_count = sum(agent.basis is RetirementBasis.PASEO_ARCHIVED for agent in retired_agents)
    return CutoverReport(False, retired, archived_count, len(retired_agents) - archived_count,
                         legacy_count, native_count, missing)


async def run(store: Store, paseo: PaseoControl) -> CutoverReport:
    if await store.fetch_one("SELECT 1 FROM schema_migrations WHERE migration = ?", (_MARKER,)):
        return CutoverReport(True, 0, 0, 0, 0, 0, 0)
    marks = ",".join("?" for _ in _TARGETS)
    rows = await store.fetch_all(
        "SELECT a.agent_ref, a.agent_id, a.legacy_ref FROM agents a JOIN sessions s USING (session_id) "
        f"WHERE s.scenario IN ({marks}) ORDER BY s.session_id, a.created_at", _TARGETS,
    )
    inventory = set(await paseo.all_agent_ids()) if rows else set()
    retired_agents: list[RetiredAgent] = []
    for raw in rows:
        agent = RetirementAgent(*raw)
        agent_id = agent.agent_id
        if agent_id is None:
            found = await paseo.find_by_label(agent.ref)
            if found is None and agent.legacy_ref is not None:
                found = await paseo.find_by_label(agent.legacy_ref)
            agent_id = found.agent_id if found is not None else None
        basis = RetirementBasis.RUNTIME_RESET
        if agent_id is not None and (agent.agent_id is None or agent_id in inventory):
            if not await paseo.is_archived(agent_id):
                await paseo.archive(agent_id)
                if not await paseo.is_archived(agent_id):
                    raise PaseoError(f"cutover archive not confirmed for {agent.ref}")
            basis = RetirementBasis.PASEO_ARCHIVED
        retired_agents.append(RetiredAgent(agent.ref, agent_id, basis))
    async with store.lock:
        report = await store.transaction(lambda conn: _commit(conn, tuple(retired_agents)))
        await store.execute("CHECKPOINT")
    return report
