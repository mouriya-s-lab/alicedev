"""HTTP boundary tests for the agent command API."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from rt_support import make_env

from alicedev.api.server import InternalApi


_AUTH = {"X-Alicedev-Token": "tok"}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class StubDispatcher:
    def __init__(self) -> None:
        self.command_calls: list[str] = []
        self.run_calls: list[Any] = []
        self.commands_result = SimpleNamespace(status=207, body={"commands": ["需求"]})
        self.run_result = SimpleNamespace(status=409, body={"error": "agent_not_current"})

    async def agent_commands(self, agent_ref: str) -> Any:
        self.command_calls.append(agent_ref)
        return self.commands_result

    async def run_agent(self, call: Any) -> Any:
        self.run_calls.append(call)
        return self.run_result


def make_api(env: Any, dispatcher: StubDispatcher) -> InternalApi:
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
        dispatcher=dispatcher,
    )


def test_commands_auth_and_dispatch_result_mapping(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            dispatcher = StubDispatcher()
            async with TestClient(TestServer(make_api(env, dispatcher).app)) as client:
                denied_get = await client.get("/v1/commands?agent=a_ref")
                assert denied_get.status == 401
                assert await denied_get.json() == {"error": "unauthorized"}
                denied_post = await client.post(
                    "/v1/commands",
                    json={"agent": "a_ref", "call_id": "c1", "text": "/需求 x"},
                )
                assert denied_post.status == 401
                assert await denied_post.json() == {"error": "unauthorized"}
                assert dispatcher.command_calls == [] and dispatcher.run_calls == []

                commands = await client.get("/v1/commands?agent=a_ref", headers=_AUTH)
                assert commands.status == 207
                assert await commands.json() == {"commands": ["需求"]}
                assert dispatcher.command_calls == ["a_ref"]

                payload = {
                    "agent": "a_ref",
                    "call_id": "c1",
                    "text": "/需求 支持中文界面",
                    "chat": "telegram:other",
                    "quote": "m_1234567890",
                    "extra": "ignored",
                }
                result = await client.post("/v1/commands", headers=_AUTH, json=payload)
                assert result.status == 409
                assert await result.json() == {"error": "agent_not_current"}
                call = dispatcher.run_calls[0]
                assert (
                    call.agent_ref,
                    call.call_id,
                    call.text,
                    call.chat_key,
                    call.quote,
                ) == ("a_ref", "c1", "/需求 支持中文界面", "telegram:other", "m_1234567890")

                optional_omitted = await client.post(
                    "/v1/commands",
                    headers=_AUTH,
                    json={"agent": "a_ref", "call_id": "c2", "text": "/会话 %3"},
                )
                assert optional_omitted.status == 409
                omitted_call = dispatcher.run_calls[1]
                assert omitted_call.chat_key is None and omitted_call.quote is None
        finally:
            await env.close()

    run(main())


def test_commands_reject_invalid_payloads_before_dispatch(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            dispatcher = StubDispatcher()
            async with TestClient(TestServer(make_api(env, dispatcher).app)) as client:
                for path in ("/v1/commands", "/v1/commands?agent="):
                    response = await client.get(path, headers=_AUTH)
                    assert response.status == 400
                    assert await response.json() == {"error": "invalid_payload"}

                valid = {"agent": "a_ref", "call_id": "c1", "text": "/需求 x"}
                invalid_payloads = []
                for field in ("agent", "call_id", "text"):
                    invalid_payloads.append({key: value for key, value in valid.items() if key != field})
                    for value in (None, 7, [], ""):
                        invalid_payloads.append({**valid, field: value})
                for field in ("chat", "quote"):
                    for value in (None, 7, []):
                        invalid_payloads.append({**valid, field: value})
                invalid_payloads.append([])

                for payload in invalid_payloads:
                    response = await client.post("/v1/commands", headers=_AUTH, json=payload)
                    assert response.status == 400
                    assert await response.json() == {"error": "invalid_payload"}

                malformed = await client.post(
                    "/v1/commands",
                    headers={**_AUTH, "Content-Type": "application/json"},
                    data="{",
                )
                assert malformed.status == 400
                assert await malformed.json() == {"error": "invalid_payload"}
                assert dispatcher.command_calls == [] and dispatcher.run_calls == []
        finally:
            await env.close()

    run(main())


def test_commands_return_draining_response(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        try:
            dispatcher = StubDispatcher()
            api = make_api(env, dispatcher)
            api._draining = True
            async with TestClient(TestServer(api.app)) as client:
                get_result = await client.get("/v1/commands?agent=a_ref", headers=_AUTH)
                assert get_result.status == 503
                assert await get_result.json() == {"error": "draining"}
                post_result = await client.post(
                    "/v1/commands",
                    headers=_AUTH,
                    json={"agent": "a_ref", "call_id": "c1", "text": "/需求 x"},
                )
                assert post_result.status == 503
                assert await post_result.json() == {"error": "draining"}
                assert dispatcher.command_calls == [] and dispatcher.run_calls == []
        finally:
            await env.close()

    run(main())
