"""Unit tests: paseo CLI parsing/argv, mainsync, outbox ordering/retry/render, inbound markers."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from rt_support import CHAT, GIT_NO_MARKERS, FakePaseo, GitResult, install_fake_astrbot, make_env

from alicedev.actions.inbound import QuotedMessage, exact_github_url
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


def test_cli_control_argv() -> None:
    calls: list[list[str]] = []
    replies = {"run": RUN, "status": STATUS, "ls": LS, "inspect": INSPECT, "workspace": WS}

    async def runner(argv, timeout):
        calls.append(list(argv))
        verb = argv[6] if argv[5] == "paseo" else argv[5]  # docker exec -u paseo <c> <bin> <verb>
        return 0, replies.get(verb, ""), ""

    async def main() -> None:
        c = CliPaseoControl(container="pc", runner=runner)
        h = await c.create(agent_ref="a_x", provider="omp/m", cwd="/w", title="t",
                           initial_prompt="/chat_ingress {}", workspace_id="ws1")
        assert (h.workspace_id, h.server_id) == ("ws1", "srv-abc")
        assert calls[0] == ["docker", "exec", "-u", "paseo", "pc", "paseo", "run", "/chat_ingress {}", "--background",
                            "--provider", "omp/m", "--cwd", "/w", "--title", "t",
                            "--label", "alicedev=a_x", "--workspace", "ws1", "--json"]
        await c.send("ag1", "/chat_ingress {}")
        assert calls[-1] == ["docker", "exec", "-u", "paseo", "pc", "paseo", "send", "--no-wait", "ag1",
                             "--prompt", "/chat_ingress {}"]
        assert (await c.find_by_label("a_x")).agent_id.startswith("4be1c0de")
        assert calls[-1][6:] == ["ls", "-a", "-g", "--label", "alicedev=a_x", "--json"]
        assert await c.status("ag1") is AgentStatus.RUNNING
        await c.archive("ag1")
        assert calls[-1][6:] == ["archive", "--force", "ag1"]
        await c.worktree_create(repo="/workspace/alicedev", base_ref="main", slug="s12")
        assert calls[-1][6:] == ["workspace", "create", "--isolation", "worktree", "--path",
                                 "/workspace/alicedev", "--mode", "branch-off", "--base", "main",
                                 "--worktree-slug", "s12", "--new-branch", "alicedev/s12",
                                 "--title", "s12", "--json"]
        await c.workspace_local("/workspace/openalice", "需求")
        assert calls[-1][6:11] == ["workspace", "create", "--isolation", "local", "--path"]
        await c.git("/workspace/alicedev", "rev-parse", "HEAD")
        assert calls[-1] == ["docker", "exec", "-u", "paseo", "pc", "git", "-C",
                             "/workspace/alicedev", "rev-parse", "HEAD"]

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
