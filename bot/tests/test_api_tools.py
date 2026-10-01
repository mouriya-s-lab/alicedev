"""Typed tool HTTP boundaries over the real service and temporary DuckDB Store."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from rt_support import ADMIN, CHAT, USER, Env, make_env

from alicedev.api.server import InternalApi
from alicedev.domain import JsonValue
from alicedev.store.agents_repo import AgentRowStatus
from alicedev.store.requirements_repo import RequirementSourceKind
from alicedev.store.sessions_repo import SessionRow


_AUTH = {"X-Alicedev-Token": "tok"}
_AGENT = "a_current"


def make_api(env: Env) -> InternalApi:
    return InternalApi(
        config=env.config,
        intake=env.intake,
        sessions=env.sessions,
        agents=env.agents,
        scheduler=env.scheduler,
        outbox=env.outbox,
        registry=lambda: env.registry,
        platforms_fn=lambda: ["telegram"],
        generation=7,
        tools=env.tools,
    )


async def make_caller(env: Env, *, scenario_name: str = "investigate") -> SessionRow:
    scenario = env.scheduler.scenario(scenario_name)
    assert scenario is not None
    principal = ADMIN if scenario_name == "steward" else USER
    async with env.store.lock:
        session = await env.sessions.create(
            chat_key=CHAT,
            scenario=scenario_name,
            name="HTTP tools caller",
            created_by=principal,
            input={"text": "caller", "sender": {"id": principal, "name": "HTTP caller"}},
            state=scenario.initial,
            assigned_by=None,
        )
        await env.sessions.set_current(CHAT, session.session_id)
        await env.agents.create_creating(
            agent_ref=_AGENT, session_id=session.session_id,
            state=session.state, provider=scenario.provider,
        )
        await env.agents.set_active(
            _AGENT, agent_id="paseo-caller", workspace_id=None, server_id=None,
        )
    return session




def test_requirement_add_persists_principal_fact_and_replays_without_sessions(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            session = await make_caller(env)
            before_sessions = await env.store.fetch_all("SELECT * FROM sessions ORDER BY session_id")
            before_agents = await env.store.fetch_all("SELECT * FROM agents ORDER BY agent_ref")
            before_current = await env.sessions.current(CHAT)
            async with TestClient(TestServer(make_api(env).app)) as client:
                payload = {"agent": _AGENT, "call_id": "req-1", "args": {"text": "支持中文界面"}}
                response = await client.post("/v1/tools/requirement_add", headers=_AUTH, json=payload)
                assert response.status == 200
                body = await response.json()
                record_id = body["data"]["id"]
                assert type(record_id) is int and record_id > 0
                assert body == {"result": "ok", "data": {"id": record_id}}
                record = await env.requirements.get(record_id)
                assert record is not None
                assert (record.chat_key, record.author_key, record.author_name, record.text) == (
                    CHAT, USER, "HTTP caller", "支持中文界面",
                )
                assert record.status == "open" and record.images == () and record.quoted is None
                assert (record.source.kind, record.source.ref) == (
                    RequirementSourceKind.AGENT_CALL, f"{_AGENT}:req-1",
                )
                replay = await client.post("/v1/tools/requirement_add", headers=_AUTH, json=payload)
                assert replay.status == 200 and await replay.json() == body
                assert await env.store.fetch_one("SELECT COUNT(*) FROM requirements") == (1,)
                assert await env.requirements.get(record_id) == record
                listed = await client.post(
                    "/v1/tools/requirements_list", headers=_AUTH,
                    json={"agent": _AGENT, "call_id": "read-1", "args": {}},
                )
                assert listed.status == 200
                page = await listed.json()
                assert page["result"] == "ok" and page["data"]["total"] == 1
                assert [item["id"] for item in page["data"]["items"]] == [record_id]
                assert page["data"]["items"][0]["text"] == record.text
                # A different real call with identical text is a separate requirement, not text deduplication.
                distinct = await client.post(
                    "/v1/tools/requirement_add", headers=_AUTH, json={**payload, "call_id": "req-2"},
                )
                assert distinct.status == 200
                distinct_id = (await distinct.json())["data"]["id"]
                assert distinct_id != record_id
                assert await env.store.fetch_all("SELECT id, text FROM requirements ORDER BY id") == [
                    (record_id, record.text), (distinct_id, record.text),
                ]
            assert await env.store.fetch_all("SELECT * FROM sessions ORDER BY session_id") == before_sessions
            assert await env.store.fetch_all("SELECT * FROM agents ORDER BY agent_ref") == before_agents
            assert await env.sessions.current(CHAT) == before_current
            assert before_current is not None and before_current.session_id == session.session_id
            assert env.paseo.created == [] and env.paseo.sent == []
            assert await env.store.fetch_one("SELECT COUNT(*) FROM favorites") == (0,)
        finally:
            await env.close()

    asyncio.run(main())


def test_tools_reject_malformed_envelopes_args_and_queries(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            await make_caller(env, scenario_name="steward")
            async with TestClient(TestServer(make_api(env).app)) as client:
                baseline = await client.get("/v1/tools", params={"agent": _AGENT}, headers=_AUTH)
                assert baseline.status == 200
                for query in ("", "?agent=", "?agent=%20", "?agent=a_current&agent=a_current", "?agent=a_current&extra=1"):
                    response = await client.get(f"/v1/tools{query}", headers=_AUTH)
                    assert (response.status, await response.json()) == (400, {"error": "invalid_payload"}), query

                valid: dict[str, JsonValue] = {"agent": _AGENT, "call_id": "invalid-call", "args": {"text": "not stored"}}
                invalid: list[JsonValue] = [None, [], "text", 1, True, {**valid, "extra": 1}]
                for field in ("agent", "call_id", "args"):
                    invalid.append({key: value for key, value in valid.items() if key != field})
                for field in ("agent", "call_id"):
                    for value in (None, 7, True, [], {}, "", " \t"):
                        invalid.append({**valid, field: value})
                for args in (None, [], "text", 1, True, {}, {"text": "x", "extra": 1}):
                    invalid.append({**valid, "args": args})
                for field in ("text", "chat", "quote"):
                    for value in (None, 7, True, [], {}, "", " \t"):
                        invalid.append({**valid, "args": {"text": "x", field: value}})
                for payload in invalid:
                    response = await client.post("/v1/tools/requirement_add", headers=_AUTH, json=payload)
                    assert (response.status, await response.json()) == (400, {"error": "invalid_payload"}), payload
                for malformed in ("{", "not-json", '{"agent":"a_current"'):
                    response = await client.post(
                        "/v1/tools/requirement_add", headers={**_AUTH, "Content-Type": "application/json"}, data=malformed,
                    )
                    assert (response.status, await response.json()) == (400, {"error": "invalid_payload"})
                for name, field in (("session_get", "no"), ("requirements_list", "page"), ("session_list", "page")):
                    for value in (True, False, 0, -1, 1.0, "1", None, []):
                        response = await client.post(
                            f"/v1/tools/{name}", headers=_AUTH,
                            json={**valid, "args": {field: value}},
                        )
                        assert (response.status, await response.json()) == (400, {"error": "invalid_payload"}), (name, value)
                for value in (0, 1, "true", None, []):
                    response = await client.post(
                        "/v1/tools/session_list", headers=_AUTH,
                        json={**valid, "args": {"include_ended": value}},
                    )
                    assert (response.status, await response.json()) == (400, {"error": "invalid_payload"}), value
                unknown = await client.post("/v1/tools/not_a_tool", headers=_AUTH, json=valid)
                assert (unknown.status, await unknown.json()) == (400, {"error": "unknown_tool"})
            assert await env.store.fetch_one("SELECT COUNT(*) FROM requirements") == (0,)
            assert await env.store.fetch_one("SELECT COUNT(*) FROM sessions") == (1,)
            assert env.paseo.created == []
        finally:
            await env.close()

    asyncio.run(main())


@pytest.mark.parametrize("invalid_caller", ["unknown", "state_changed", "archived", "superseded"])
def test_tools_reject_unknown_and_noncurrent_agents(tmp_path: Path, invalid_caller: str) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            session = await make_caller(env)
            async with TestClient(TestServer(make_api(env).app)) as client:
                baseline = await client.get("/v1/tools", params={"agent": _AGENT}, headers=_AUTH)
                assert baseline.status == 200
                ref = _AGENT
                if invalid_caller == "unknown":
                    ref = "a_unknown"
                elif invalid_caller == "state_changed":
                    await env.sessions.set_state(session.session_id, "archived")
                elif invalid_caller == "archived":
                    await env.agents.set_status(ref, AgentRowStatus.ARCHIVED)
                elif invalid_caller == "superseded":
                    await env.agents.create_creating(
                        agent_ref="a_replacement", session_id=session.session_id,
                        state=session.state, provider="test-provider",
                    )
                    latest = await env.agents.for_state(session.session_id, session.state)
                    assert latest is not None and latest.agent_ref == "a_replacement"
                expected = (404, {"error": "agent_unknown"}) if invalid_caller == "unknown" else (
                    409, {"error": "agent_not_current"},
                )
                discovery = await client.get("/v1/tools", params={"agent": ref}, headers=_AUTH)
                assert (discovery.status, await discovery.json()) == expected
                invocation = await client.post(
                    "/v1/tools/requirement_add", headers=_AUTH,
                    json={"agent": ref, "call_id": "rejected", "args": {"text": "not stored"}},
                )
                assert (invocation.status, await invocation.json()) == expected
            assert await env.store.fetch_one("SELECT COUNT(*) FROM requirements") == (0,)
            assert env.paseo.created == []
        finally:
            await env.close()

    asyncio.run(main())


def test_tools_enforce_allowlist_and_retired_command_routes(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            await make_caller(env)
            async with TestClient(TestServer(make_api(env).app)) as client:
                forbidden = await client.post(
                    "/v1/tools/session_list", headers=_AUTH,
                    json={"agent": _AGENT, "call_id": "denied", "args": {}},
                )
                assert (forbidden.status, await forbidden.json()) == (403, {"error": "tool_not_allowed"})
                old_get = await client.get("/v1/commands?agent=a_current", headers=_AUTH)
                assert old_get.status == 404
                old_post = await client.post(
                    "/v1/commands", headers=_AUTH,
                    json={"agent": _AGENT, "call_id": "legacy", "text": "/需求 not stored"},
                )
                assert old_post.status == 404
            assert await env.store.fetch_one("SELECT COUNT(*) FROM requirements") == (0,)
            assert await env.store.fetch_one("SELECT COUNT(*) FROM sessions") == (1,)
        finally:
            await env.close()

    asyncio.run(main())


@pytest.mark.parametrize("draining", [False, True])
def test_tools_authentication_and_draining_prevent_writes(tmp_path: Path, draining: bool) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            await make_caller(env)
            api = make_api(env)
            api._draining = draining
            headers = _AUTH if draining else {}
            expected = (503, {"error": "draining"}) if draining else (401, {"error": "unauthorized"})
            async with TestClient(TestServer(api.app)) as client:
                discovery = await client.get("/v1/tools", params={"agent": _AGENT}, headers=headers)
                assert (discovery.status, await discovery.json()) == expected
                invocation = await client.post(
                    "/v1/tools/requirement_add", headers=headers,
                    json={"agent": _AGENT, "call_id": "no-write", "args": {"text": "not stored"}},
                )
                assert (invocation.status, await invocation.json()) == expected
            assert await env.store.fetch_one("SELECT COUNT(*) FROM requirements") == (0,)
            assert env.paseo.created == []
        finally:
            await env.close()

    asyncio.run(main())
