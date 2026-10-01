"""Unit tests: paseo CLI boundaries, mainsync, outbox ordering/retry/render, inbound markers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from rt_support import CHAT, GIT_NO_MARKERS, FakePaseo, GitResult, install_fake_astrbot, make_env

from alicedev.actions.inbound import QuotedMessage, exact_github_url
from alicedev.outbox.render import is_private_chat
from alicedev.paseo import cli_json, mainsync
from alicedev.paseo.cli import CliPaseoControl
from alicedev.paseo.control import AgentStatus, PaseoError

# --- paseo CLI JSON (shapes recorded from paseo 0.8.0 on prod) -------------------------

RUN = '{"agentId":"4be1c0de-1111-2222-3333-444455556666","status":"running","provider":"omp-alicedev","cwd":"/workspace/openalice","title":"需求 · x"}'
LS = '[{"id":"4be1c0de-1111-2222-3333-444455556666","shortId":"4be1c0d","name":"需求 · x","provider":"omp-alicedev","thinking":"high","status":"idle","cwd":"/workspace/openalice","created":"2026-09-23T03:00:00Z"}]'
INSPECT = '{"Id":"4be1c0de","Name":"x","Provider":"omp-alicedev","Model":"m","Status":"running","Archived":false,"Cwd":"/w","Worktree":null,"PendingPermissions":[]}'
WS = '{"workspaceId":"ws_01","project":"p1","name":"s12","isolation":"worktree","cwd":"/root/.paseo/worktrees/s12"}'
STATUS = '{"serverId":"srv-abc","daemonVersion":"0.8.0"}'
ERROR = '{"error":{"code":"NOT_FOUND","message":"agent not found"}}'


def test_cli_json_shapes() -> None:
    assert cli_json.run_agent_id(cli_json.loads(RUN)).startswith("4be1c0de")
    assert cli_json.ls_agent_ids(cli_json.loads(LS)) == ["4be1c0de-1111-2222-3333-444455556666"]
    assert cli_json.inspect_status(cli_json.loads(INSPECT)) is AgentStatus.RUNNING
    assert cli_json.inspect_status({"Status": "idle", "Archived": True}) is AgentStatus.CLOSED
    assert cli_json.inspect_status({"Status": "running", "PendingPermissions": [{}]}) is AgentStatus.PERMISSION
    ws = cli_json.workspace_ref(cli_json.loads(WS))
    assert (ws.workspace_id, ws.cwd) == ("ws_01", "/root/.paseo/worktrees/s12")
    assert cli_json.server_id(cli_json.loads(STATUS)) == "srv-abc"
    assert cli_json.error_of(cli_json.loads(ERROR)) == "NOT_FOUND: agent not found"
    assert cli_json.loads("warn: something\n" + STATUS)["serverId"] == "srv-abc"
    with pytest.raises(cli_json.CliShapeError):
        cli_json.loads("")
    with pytest.raises(cli_json.CliShapeError):
        cli_json.run_agent_id({"status": "running"})
    assert cli_json.map_status("weird") is AgentStatus.UNKNOWN


def test_cli_control_domain_results() -> None:
    replies = {"run": RUN, "status": STATUS, "ls": LS, "inspect": INSPECT, "workspace": WS}

    async def runner(argv: Sequence[str], timeout: float) -> tuple[int, str, str]:
        if "git" in argv:
            return 0, "abc123\n", ""
        verb = next(arg for arg in argv if arg in replies)
        return 0, replies[verb], ""

    async def main() -> None:
        c = CliPaseoControl(container="pc", runner=runner)
        h = await c.create(agent_ref="a_x", provider="omp/m", cwd="/w", title="t",
                           initial_prompt="/chat_ingress {}", workspace_id="ws1")
        assert (h.agent_id, h.workspace_id, h.server_id) == (
            "4be1c0de-1111-2222-3333-444455556666", "ws1", "srv-abc",
        )
        recovered = await c.find_by_label("a_x")
        assert recovered is not None
        assert (recovered.agent_id, recovered.server_id) == (h.agent_id, h.server_id)
        assert await c.status(h.agent_id) is AgentStatus.RUNNING
        worktree = await c.worktree_create(repo="/workspace/alicedev", base_ref="main", slug="s12")
        assert (worktree.workspace_id, worktree.cwd) == ("ws_01", "/root/.paseo/worktrees/s12")
        local = await c.workspace_local("/workspace/openalice", "需求")
        assert (local.workspace_id, local.cwd) == ("ws_01", "/root/.paseo/worktrees/s12")
        assert await c.git("/workspace/alicedev", "rev-parse", "HEAD") == GitResult(0, "abc123\n", "")

    asyncio.run(main())


def test_cli_control_errors() -> None:
    async def runner(argv, timeout):
        return 1, ERROR, ""

    async def main() -> None:
        c = CliPaseoControl(runner=runner)
        with pytest.raises(PaseoError, match="NOT_FOUND"):
            await c.status("nope")
        with pytest.raises(PaseoError):
            await c.send("nope", "x")

    asyncio.run(main())


def _cli_with_output(stdout: str, *, returncode: int = 0) -> CliPaseoControl:
    async def runner(argv: Sequence[str], timeout: float) -> tuple[int, str, str]:
        return returncode, stdout, ""

    return CliPaseoControl(runner=runner)


def test_cli_all_agent_ids_includes_archived_and_other_workspaces() -> None:
    current = json.loads(LS)[0]
    archived = {**current, "id": "archived-agent", "status": "closed"}
    other = {**current, "id": "other-workspace-agent", "cwd": "/workspace/other",
             "provider": "another-provider", "status": "running"}

    async def runner(argv: Sequence[str], timeout: float) -> tuple[int, str, str]:
        visible = [current]
        if "-a" in argv:
            visible.append(archived)
        if "-g" in argv:
            visible.append(other)
        return 0, json.dumps(visible), ""

    c = CliPaseoControl(runner=runner)
    assert asyncio.run(c.all_agent_ids()) == [
        current["id"], "archived-agent", "other-workspace-agent",
    ]


def test_cli_all_agent_ids_empty_success() -> None:
    assert asyncio.run(_cli_with_output("[]").all_agent_ids()) == []


@pytest.mark.parametrize(
    "bad_row",
    [
        '{"status":"closed"}',
        '{"id":""}',
        '{"id":null}',
        '{"id":42}',
        '{"id":true}',
        "null",
        '"agent-id"',
        "[]",
    ],
)
def test_cli_all_agent_ids_rejects_malformed_rows_without_partial_results(bad_row: str) -> None:
    valid_row = LS[1:-1]
    c = _cli_with_output(f"[{valid_row},{bad_row}]")
    with pytest.raises(PaseoError):
        asyncio.run(c.all_agent_ids())


@pytest.mark.parametrize(
    ("stdout", "returncode"),
    [
        ("", 0),
        ("not JSON", 0),
        ("{}", 0),
        ("null", 0),
        (ERROR, 0),
        (ERROR, 1),
        ("[]", 1),
    ],
)
def test_cli_all_agent_ids_does_not_treat_query_failure_as_empty(stdout: str, returncode: int) -> None:
    c = _cli_with_output(stdout, returncode=returncode)
    with pytest.raises(PaseoError):
        asyncio.run(c.all_agent_ids())


@pytest.mark.parametrize("count", [199, 200, 201])
def test_cli_all_agent_ids_fails_closed_at_unpaged_server_limit(count: int) -> None:
    recorded_row = json.loads(LS)[0]
    ids = [f"agent-{index}" for index in range(count)]
    stdout = json.dumps([{**recorded_row, "id": agent_id} for agent_id in ids])
    c = _cli_with_output(stdout)
    if count < 200:
        assert asyncio.run(c.all_agent_ids()) == ids
    else:
        with pytest.raises(PaseoError):
            asyncio.run(c.all_agent_ids())


@pytest.mark.parametrize("status", ["closed", "running"])
@pytest.mark.parametrize("archived", [False, True])
def test_cli_is_archived_uses_explicit_boolean_not_status(status: str, archived: bool) -> None:
    stdout = json.dumps({**json.loads(INSPECT), "Status": status, "Archived": archived})
    assert asyncio.run(_cli_with_output(stdout).is_archived("agent-id")) is archived


@pytest.mark.parametrize(
    "stdout",
    [
        '{"Status":"closed"}',
        '{"Status":"closed","archived":true}',
        '{"Status":"closed","Archived":null}',
        '{"Status":"closed","Archived":0}',
        '{"Status":"closed","Archived":1}',
        '{"Status":"closed","Archived":"true"}',
        '{"Status":"closed","Archived":"false"}',
        '{"Status":"closed","Archived":[]}',
        '{"Status":"closed","Archived":{}}',
        "[]",
    ],
)
def test_cli_is_archived_rejects_missing_or_nonboolean_archive_fact(stdout: str) -> None:
    with pytest.raises(PaseoError):
        asyncio.run(_cli_with_output(stdout).is_archived("agent-id"))


@pytest.mark.parametrize("returncode", [0, 1])
def test_cli_is_archived_inspect_failed_is_not_absence(returncode: int) -> None:
    stdout = '{"error":{"code":"INSPECT_FAILED","message":"agent not found"}}'
    c = _cli_with_output(stdout, returncode=returncode)
    with pytest.raises(PaseoError, match="INSPECT_FAILED"):
        asyncio.run(c.is_archived("agent-id"))


def test_cli_archive_failure_propagates() -> None:
    c = _cli_with_output(ERROR, returncode=1)
    with pytest.raises(PaseoError):
        asyncio.run(c.archive("agent-id"))


# --- mainsync ---------------------------------------------------------------------------


def _paseo() -> FakePaseo:
    p = FakePaseo()
    p.git_overrides.update(GIT_NO_MARKERS)
    return p


def test_mainsync_aligned_and_failures() -> None:
    async def main() -> None:
        p = _paseo()
        assert await mainsync.align(p, "/r") == mainsync.Aligned("abc123")
        cases = {
            ("symbolic-ref", "--quiet", "--short", "HEAD"): (GitResult(0, "feature", ""), "不是 main"),
            ("status", "--porcelain=v1", "--untracked-files=all"): (GitResult(0, "?? x", ""), "未提交"),
            ("rev-parse", "--verify", "--quiet", "MERGE_HEAD"): (GitResult(0, "sha", ""), "MERGE_HEAD"),
            ("fetch", "--prune", "origin", "main"): (GitResult(1, "", "net"), "fetch"),
            ("merge-base", "HEAD", "origin/main"): (GitResult(0, "old", ""), "不能快进"),
        }
        for args, (result, needle) in cases.items():
            p = _paseo()
            p.git_overrides[args] = result
            outcome = await mainsync.align(p, "/r")
            assert isinstance(outcome, mainsync.SyncFailed) and needle in outcome.reason, (args, outcome)

    asyncio.run(main())


# --- outbox --------------------------------------------------------------------------------


def test_outbox_fifo_per_chat_and_retry(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        for i in range(3):
            await env.outbox.enqueue_text(CHAT, f"a{i}")
        await env.outbox.enqueue_text("other", "b0")
        await env.outbox.enqueue_text(CHAT, "")  # empty text is skipped
        env.sender.fail_next = 1
        assert await env.outbox.drain_once() == 1  # CHAT head failed, "other" delivered
        assert [c[0].text for _, c in env.sender.sent] == ["b0"]
        head = (await env.outbox.repo.due_heads())
        assert head == []  # CHAT head is backing off and blocks the chat
        await env.store.execute("UPDATE outbox SET next_attempt_at = NULL")
        while await env.outbox.drain_once():
            pass
        assert [c[0].text for ch, c in env.sender.sent if ch == CHAT] == ["a0", "a1", "a2"]
        assert await env.outbox.repo.pending_count() == 0
        await env.close()

    asyncio.run(main())


def test_outbox_gives_up_after_max_attempts(tmp_path: Path) -> None:
    from alicedev.store.outbox_repo import MAX_ATTEMPTS

    async def main() -> None:
        env = await make_env(tmp_path)
        await env.outbox.enqueue_text(CHAT, "x")
        await env.outbox.enqueue_text(CHAT, "y")
        env.sender.fail_next = MAX_ATTEMPTS
        for _ in range(MAX_ATTEMPTS):
            await env.store.execute("UPDATE outbox SET next_attempt_at = NULL")
            await env.outbox.drain_once()
        rows = await env.store.fetch_all("SELECT payload, state, attempts FROM outbox ORDER BY seq")
        assert rows[0][1] == "failed" and rows[0][2] == MAX_ATTEMPTS
        await env.store.execute("UPDATE outbox SET next_attempt_at = NULL")
        await env.outbox.drain_once()
        assert [c[0].text for _, c in env.sender.sent] == ["y"]
        await env.close()

    asyncio.run(main())


def test_outbox_render_ai_kinds(tmp_path: Path) -> None:
    install_fake_astrbot()

    async def main() -> None:
        env = await make_env(tmp_path)
        r = env.outbox._renderer
        marker = "%3 需求 · x"
        out = await r.render({"type": "ai", "marker": marker, "max_text_chars": 5,
                              "reply": {"kind": "text", "text": "0123456789"}})
        assert out[0].text == marker and env.cards.rendered[-1][0] == "generic_card"
        out = await r.render({"type": "ai", "marker": marker, "max_text_chars": 600,
                              "reply": {"kind": "image_template", "template": "generic_card",
                                        "fields": {"text": "t"}}})
        assert out[0].text == marker and type(out[1]).__name__ == "Image"
        png = tmp_path / "reports" / "a.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        out = await r.render({"type": "ai", "marker": marker, "max_text_chars": 600,
                              "reply": {"kind": "image", "paths": [str(png)], "caption": "证据"}})
        assert out[0].text == f"{marker}\n证据" and out[1].file.startswith("base64://")
        out = await r.render({"type": "ai", "marker": marker, "max_text_chars": 600,
                              "reply": {"kind": "file", "path": "/x/r.md"},
                              "published": {"basename": "r.md", "path": "/p/r.md",
                                            "is_markdown": True, "url": "https://dev.example/r/r.md"}})
        assert out[0].text == f"{marker}\nhttps://dev.example/r/r.md"
        out = await r.render({"type": "at_text", "platform_id": "5", "name": "小明", "text": " url"})
        assert (out[0].qq, out[1].text) == ("5", " url")
        out = await r.render({"type": "at_text", "platform_id": "5", "name": "小明", "text": " url"}, private=True)
        assert [type(c).__name__ for c in out] == ["Plain"] and out[0].text == "url"
        assert is_private_chat("qq:FriendMessage:5") and not is_private_chat("qq:GroupMessage:9")
        assert not is_private_chat("telegram:GroupMessage:-100") and not is_private_chat("garbage")
        await env.close()

    asyncio.run(main())


# --- inbound --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "no"),
    [("%3 需求 · x\n正文", 3), ("％12 名称", 12), ("  %7", 7), ("没有标记", None), ("100% 完成", None)],
)
def test_quoted_marker(text: str, no: int | None) -> None:
    assert QuotedMessage("", "", "", text).session_marker == no


def test_exact_github_url() -> None:
    assert exact_github_url("https://github.com/TraderAlice/OpenAlice/issues/12")
    assert exact_github_url("https://github.com/TraderAlice/OpenAlice/pull/3")
    assert not exact_github_url("看看 https://github.com/TraderAlice/OpenAlice/issues/12")
    assert not exact_github_url("https://github.com/TraderAlice/OpenAlice")


def test_reply_payload_image_kind() -> None:
    from alicedev.api.payloads import ImageReply, InvalidPayload, parse_reply_payload

    assert parse_reply_payload({"kind": "image", "paths": ["/r/a.png"], "caption": "c"}) == \
        ImageReply(("/r/a.png",), "c")
    with pytest.raises(InvalidPayload):
        parse_reply_payload({"kind": "image", "paths": []})
