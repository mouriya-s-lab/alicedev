"""End-to-end runtime flows over a real Store with fake paseo / platform (ARCHITECTURE §3, §6, §7)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rt_support import ADMIN, CHAT, GitResult, inbound, make_env, quote_of

from alicedev.actions.inbound import Mention, QuotedMessage
from alicedev.paseo.control import AgentStatus


def run(coro):
    return asyncio.run(coro)


def ingress(text: str) -> dict:
    assert text.startswith("/chat_ingress ")
    return json.loads(text[len("/chat_ingress "):])


# --- AI commands → sessions ----------------------------------------------------------


def test_requirement_start_send_and_reply(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        assert await env.dispatcher.handle(inbound("/需求 收藏夹按作者筛选"))
        assert await env.texts() == ["已开始 %1 需求 · 收藏夹按作者筛选"]
        row = await env.sessions.by_no(CHAT, 1)
        assert row is not None and row.state == "discussing" and row.scenario == "requirement"
        assert (await env.sessions.current(CHAT)).session_id == row.session_id
        created = env.paseo.created[0]
        assert created["provider"] == "omp-alicedev/opencode-go/muse-spark-1.3-contributor"
        assert created["cwd"] == "/workspace/openalice"
        assert created["workspace_id"] == env.paseo.workspaces[0][0]
        first = ingress(created["prompt"])
        assert first["agent"] == created["agent_ref"]
        assert "收藏夹按作者筛选" in first["text"]
        assert "chat_reply" in first["text"]  # reply instructions appended
        agent = await env.agents.get(created["agent_ref"])
        assert agent is not None and agent.status.value == "active" and agent.state == "discussing"

        # Addressed natural message → current session.
        assert await env.dispatcher.handle(inbound("第二点展开说说", addressed=True))
        assert await env.texts() == []  # send.ok is empty
        sent_id, sent_text = env.paseo.sent[-1]
        assert sent_id == created["agent_id"] and ingress(sent_text)["text"] == "第二点展开说说"

        # AI reply → outbox → platform, marker first.
        res = await env.intake.handle({"agent": created["agent_ref"], "reply_id": "r1", "msgs": [],
                                       "reply": {"kind": "text", "text": "好的"}})
        assert res.status == 202 and res.body == {"status": "queued"}
        assert await env.texts() == ["%1 需求 · 收藏夹按作者筛选\n好的"]
        replay = await env.intake.handle({"agent": created["agent_ref"], "reply_id": "r1", "msgs": [],
                                          "reply": {"kind": "text", "text": "好的"}})
        assert replay.body == {"status": "replayed"} and await env.texts() == []
        conflict = await env.intake.handle({"agent": created["agent_ref"], "reply_id": "r1",
                                            "msgs": [], "reply": {"kind": "text", "text": "变了"}})
        assert (conflict.status, conflict.body["error"]) == (409, "reply_id_conflict")
        await env.close()

    run(main())


def test_duplicate_platform_message_is_silent(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        await env.dispatcher.handle(inbound("/需求 a", message_id="dup"))
        await env.dispatcher.handle(inbound("/需求 a", message_id="dup"))
        assert await env.texts() == ["已开始 %1 需求 · a"]
        assert len(env.paseo.created) == 1
        await env.close()

    run(main())


def test_session_resolution_explicit_quoted_current(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        await env.dispatcher.handle(inbound("/需求 一"))
        await env.dispatcher.handle(inbound("/需求 二"))
        await env.drain()
        one, two = env.paseo.created[0]["agent_id"], env.paseo.created[1]["agent_id"]
        # current = %2
        await env.dispatcher.handle(inbound("/继续 默认"))
        assert env.paseo.sent[-1][0] == two
        await env.dispatcher.handle(inbound("/继续 %1 显式"))
        assert env.paseo.sent[-1][0] == one
        assert "显式" in ingress(env.paseo.sent[-1][1])["text"]
        # Quoting an AI message of %1 (marker line) targets %1.
        await env.dispatcher.handle(inbound("/继续 引用", quoted=quote_of("%1 需求 · 一\n回复")))
        assert env.paseo.sent[-1][0] == one
        # Sending made %1 current.
        assert (await env.sessions.current(CHAT)).no == 1
        await env.dispatcher.handle(inbound("/继续 %9 不存在"))
        assert await env.texts() == ["找不到这个会话。可以用 /会话列表 查看本群会话。"]
        await env.close()

    run(main())


def test_busy_agent(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        await env.dispatcher.handle(inbound("/需求 x"))
        await env.drain()
        env.paseo.statuses[env.paseo.created[0]["agent_id"]] = AgentStatus.RUNNING
        await env.dispatcher.handle(inbound("/继续 y"))
        assert await env.texts() == ["%1 的 AI 还在处理上一条消息，稍后再试。"]
        await env.close()

    run(main())


def test_agent_create_failure_marks_failed(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        env.paseo.fail_create = True
        await env.dispatcher.handle(inbound("/需求 x"))
        texts = await env.texts()
        assert texts == ["%1 创建失败：启动 agent 失败：provider unavailable"]
        row = await env.sessions.by_no(CHAT, 1)
        assert row.state == "failed"
        assert await env.sessions.current(CHAT) is None
        await env.close()

    run(main())


# --- program commands -----------------------------------------------------------------


def test_program_commands(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        await env.dispatcher.handle(inbound("/需求 一"))
        await env.dispatcher.handle(inbound("/帮我调查 二"))
        await env.drain()
        await env.dispatcher.handle(inbound("/切换 %1"))
        assert await env.texts() == ["当前会话已切换到 %1 需求 · 一"]
        await env.dispatcher.handle(inbound("/重命名 新名字"))
        assert await env.texts() == ["%1 已改名为「新名字」"]
        # owner permission: another user may not rename or archive (silent).
        await env.dispatcher.handle(inbound("/重命名 %1 抢", user="telegram:3"))
        await env.dispatcher.handle(inbound("/归档 %1", user="telegram:3"))
        assert await env.texts() == []
        assert (await env.sessions.by_no(CHAT, 1)).name == "新名字"
        # admin may.
        await env.dispatcher.handle(inbound("/归档 %1", user=ADMIN))
        assert await env.texts() == ["已归档 %1 新名字"]
        row = await env.sessions.by_no(CHAT, 1)
        assert row.state == "archived"
        assert env.paseo.created[0]["agent_id"] in env.paseo.archived
        assert await env.sessions.current(CHAT) is None

        await env.dispatcher.handle(inbound("/会话列表"))
        await env.dispatcher.handle(inbound("/会话列表 全部"))
        await env.dispatcher.handle(inbound("/会话 %2"))
        await env.dispatcher.handle(inbound("/alicedev"))
        await env.dispatcher.handle(inbound("/alicedev 需求"))
        await env.drain()
        names = [c for c, _ in env.cards.rendered]
        assert names == ["session_list", "session_list", "session", "help", "command"]
        active_rows = env.cards.rendered[0][1]
        all_rows = env.cards.rendered[1][1]
        assert json.dumps(active_rows, ensure_ascii=False, default=str).count("%") < \
            json.dumps(all_rows, ensure_ascii=False, default=str).count("%")
        await env.close()

    run(main())


def test_usage_unknown_and_admin_only(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        await env.dispatcher.handle(inbound("/需求"))
        assert await env.texts() == ["缺少内容\n用法：/需求 <内容>"]
        assert not await env.dispatcher.handle(inbound("/不存在"))  # not addressed: other plugins
        assert await env.dispatcher.handle(inbound("/不存在", addressed=True))
        assert await env.texts() == ["未知指令：/不存在。发送 /alicedev 查看帮助。"]
        await env.dispatcher.handle(inbound("/升级bot 加个指令"))  # admin only → silent
        assert await env.texts() == [] and env.paseo.created == []
        assert not await env.dispatcher.handle(inbound("群聊闲话"))
        await env.close()

    run(main())


def test_share_links_per_mention(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        await env.dispatcher.handle(inbound("/需求 x"))
        await env.drain()
        mentions = (Mention("telegram:5", "5", "小明"), Mention("telegram:6", "6", "小红"))
        await env.dispatcher.handle(inbound("/链接 %1 @小明 @小红", user=ADMIN, mentions=mentions))
        sends = await env.drain()
        assert [type(c[0]).__name__ for _, c in sends] == ["At", "At"]
        assert [g["user_key"] for g in env.gateway.issued] == ["telegram:5", "telegram:6"]
        target = env.gateway.issued[0]["target"]
        assert target.startswith("/h/srv1/workspace/") and "open=agent%3A" in target
        tokens = await env.store.fetch_all("SELECT session_id, user_key FROM tokens_issued ORDER BY user_key")
        assert [t[1] for t in tokens] == ["telegram:5", "telegram:6"]
        await env.close()

    run(main())


# --- upgrade-bot: repo scenario, exclusive queue, human states, share ----------------------


def test_upgrade_bot_full_cycle(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        await env.dispatcher.handle(inbound("/升级bot 加 /ping", user=ADMIN))
        assert await env.texts() == ["已开始 %1 升级 · 加 /ping"]
        s1 = await env.sessions.by_no(CHAT, 1)
        assert s1.state == "working" and s1.base_sha == "abc123"
        wt = env.paseo.worktrees[0]
        assert wt == dict(ws=wt["ws"], repo="/workspace/alicedev", base="main",
                          slug=f"s{s1.session_id}", cwd=wt["cwd"])
        work = env.paseo.created[0]
        assert work["cwd"] == wt["cwd"] and work["workspace_id"] == wt["ws"]
        prompt = ingress(work["prompt"])["text"]
        assert "awaiting_approval" in prompt and "needs_human" in prompt

        # Exclusive: a second request queues.
        await env.dispatcher.handle(inbound("/升级bot 第二个", user=ADMIN))
        assert await env.texts() == ["已排队 %2 升级 · 第二个：同类会话正在进行，前一个结束后自动开始。"]
        assert (await env.sessions.by_no(CHAT, 2)).state == "queued"

        # §6: internal agent state must transition; data required.
        bad = await env.intake.handle({"agent": work["agent_ref"], "reply_id": "u0", "msgs": [],
                                       "reply": {"kind": "text", "text": "x"}})
        assert bad.body["error"] == "transition_required"
        bad = await env.intake.handle({"agent": work["agent_ref"], "reply_id": "u0", "msgs": [],
                                       "transition": {"state": "awaiting_approval", "data": {"pr": 1}},
                                       "reply": {"kind": "text", "text": "x"}})
        assert bad.body["error"] == "transition_data_missing"
        bad = await env.intake.handle({"agent": work["agent_ref"], "reply_id": "u0", "msgs": [],
                                       "transition": {"state": "active", "data": {"commit": "c"}},
                                       "reply": {"kind": "text", "text": "x"}})
        assert bad.body["error"] == "transition_not_allowed"
        bad = await env.intake.handle({"agent": work["agent_ref"], "reply_id": "u0", "msgs": [],
                                       "transition": {"state": "awaiting_approval",
                                                      "data": {"issue": 1, "pr": 2, "commit": "c1"}}})
        assert bad.body["error"] == "reply_required"
        ok = await env.intake.handle({"agent": work["agent_ref"], "reply_id": "u1", "msgs": [],
                                      "transition": {"state": "awaiting_approval",
                                                     "data": {"issue": 1, "pr": 2, "commit": "c1"}},
                                      "reply": {"kind": "text", "text": "候选就绪，请审批"}})
        assert ok.status == 202
        assert await env.texts() == ["%1 升级 · 加 /ping\n候选就绪，请审批"]
        s1 = await env.sessions.get(s1.session_id)
        assert s1.state == "awaiting_approval" and s1.data == {"issue": "1", "pr": "2", "commit": "c1"}
        assert work["agent_id"] in env.paseo.archived
        late = await env.intake.handle({"agent": work["agent_ref"], "reply_id": "u2", "msgs": [],
                                        "reply": {"kind": "text", "text": "迟到"}})
        assert (late.status, late.body["error"]) == (409, "agent_not_current")

        # Human approve (admin; quoted approval message resolves %1).
        await env.dispatcher.handle(inbound("/升级bot approve", user=ADMIN,
                                            quoted=quote_of("%1 升级 · 加 /ping\n候选就绪")))
        assert await env.texts() == ["%1 已批准，开始 merge 并部署。"]
        deploy = env.paseo.created[1]
        assert (await env.agents.get(deploy["agent_ref"])).state == "deploying"
        assert '"commit": "c1"' in ingress(deploy["prompt"])["text"] or "c1" in ingress(deploy["prompt"])["text"]

        # Deploy agent reports active → terminal: finish, worktree archived, next dispatched.
        done = await env.intake.handle({"agent": deploy["agent_ref"], "reply_id": "u3", "msgs": [],
                                        "transition": {"state": "active", "data": {"commit": "c1"}},
                                        "reply": {"kind": "text", "text": "已上线"}})
        assert done.status == 202
        texts = await env.texts()
        assert texts[0] == "%1 升级 · 加 /ping\n已上线"
        s1 = await env.sessions.get(s1.session_id)
        assert s1.state == "active"
        assert wt["ws"] in env.paseo.archived_workspaces
        s2 = await env.sessions.by_no(CHAT, 2)
        assert s2.state == "working" and len(env.paseo.worktrees) == 2
        await env.close()

    run(main())


def test_upgrade_bot_main_sync_failed_shares_link(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        env.paseo.git_overrides[("status", "--porcelain=v1", "--untracked-files=all")] = \
            GitResult(0, " M dirty.py", "")
        await env.dispatcher.handle(inbound("/升级bot x", user=ADMIN))
        texts = await env.texts()
        row = await env.sessions.by_no(CHAT, 1)
        assert row.state == "main_sync_failed"
        assert env.paseo.worktrees == [] and env.paseo.workspaces[0][1] == "/workspace/alicedev"
        # The state notice (with its link) is the only reply; start adds no "failed" line.
        assert len(texts) == 1
        assert "无法快进" in texts[0] and "https://dev.example/t/t1" in texts[0]
        assert env.gateway.issued[0]["user_key"] == ADMIN
        # A failed exclusive session does not block the next one.
        env.paseo.git_overrides.pop(("status", "--porcelain=v1", "--untracked-files=all"))
        await env.dispatcher.handle(inbound("/升级bot y", user=ADMIN))
        assert (await env.sessions.by_no(CHAT, 2)).state == "working"
        await env.close()

    run(main())


def test_needs_human_share_link_follows_ai_reply(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        await env.dispatcher.handle(inbound("/升级bot x", user=ADMIN))
        await env.drain()
        work = env.paseo.created[0]
        res = await env.intake.handle({"agent": work["agent_ref"], "reply_id": "h1", "msgs": [],
                                       "transition": {"state": "needs_human", "data": {"reason": "卡住"}},
                                       "reply": {"kind": "text", "text": "需要人工"}})
        assert res.status == 202
        assert await env.texts() == ["%1 升级 · x\n需要人工", "https://dev.example/t/t1"]
        await env.dispatcher.handle(inbound("/升级bot reject %1", user=ADMIN))
        assert await env.texts() == ["%1 已拒绝，会话结束。"]
        assert (await env.sessions.by_no(CHAT, 1)).state == "rejected"
        await env.close()

    run(main())


def test_restart_recovers_creating_agent(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        await env.dispatcher.handle(inbound("/需求 x"))
        await env.drain()
        ref = env.paseo.created[0]["agent_ref"]
        await env.store.execute("UPDATE agents SET status = 'creating', agent_id = NULL WHERE agent_ref = ?", (ref,))
        await env.scheduler.recover()
        agent = await env.agents.get(ref)
        assert agent.status.value == "active" and agent.agent_id == env.paseo.created[0]["agent_id"]
        await env.close()

    run(main())


def test_status_and_health_routes(tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from alicedev.api.server import InternalApi

    async def main() -> None:
        env = await make_env(tmp_path)
        (tmp_path / "REVISION").write_text("deadbeef\n")
        api = InternalApi(config=env.config, intake=env.intake, sessions=env.sessions,
                          agents=env.agents, scheduler=env.scheduler, outbox=env.outbox,
                          registry=lambda: env.registry, platforms_fn=lambda: ["telegram"],
                          generation=7)
        await env.dispatcher.handle(inbound("/需求 x"))
        async with TestClient(TestServer(api.app)) as client:
            health = await (await client.get("/v1/health")).json()
            assert health == {"generation": 7, "revision": "deadbeef"}
            assert (await client.get("/v1/status")).status == 401
            status = await (await client.get("/v1/status", headers={"X-Alicedev-Token": "tok"})).json()
            assert status["revision"] == "deadbeef" and status["dsl_errors"] == []
            assert status["sessions"]["active"] == 1 and status["outbox_pending"] == 1
            ref = env.paseo.created[0]["agent_ref"]
            agent = await (await client.get(f"/v1/agents/{ref}", headers={"X-Alicedev-Token": "tok"})).json()
            assert agent["session_no"] == 1 and agent["reply_spec"]["kinds"] == ["text", "image_template"]
            resp = await client.post("/v1/reply", headers={"X-Alicedev-Token": "tok"},
                                     json={"agent": ref, "reply_id": "z", "msgs": [],
                                           "reply": {"kind": "text", "text": "hi"}})
            assert resp.status == 202 and (await resp.json()) == {"status": "queued"}
        await env.close()

    run(main())


def test_favorites_and_addressed_without_session(tmp_path: Path) -> None:
    async def main() -> None:
        env = await make_env(tmp_path)
        await env.dispatcher.handle(inbound("在吗", addressed=True))
        assert await env.texts() == ["当前没有会话，请先用 /需求 或 /帮我调查 开始，或用 /会话列表 看看已有会话。"]
        await env.dispatcher.handle(inbound("/收藏夹"))
        assert await env.texts() == ["没有可显示的内容。"]
        await env.dispatcher.handle(inbound("/收藏"))
        assert await env.texts() == ["请先引用一条消息\n用法：/收藏 （引用消息）"]
        png = tmp_path / "q.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        quoted = QuotedMessage("p9", "telegram:7", "小七", "名言", (str(png),))
        await env.dispatcher.handle(inbound("/收藏", quoted=quoted))
        assert await env.texts() == ["已收藏 #1"]
        await env.dispatcher.handle(inbound("/收藏夹"))
        await env.drain()
        card, fields = env.cards.rendered[-1]
        assert card == "favorites_list" and "名言" in json.dumps(fields, ensure_ascii=False, default=str)
        await env.close()

    run(main())


def test_github_link_route(tmp_path: Path) -> None:
    from alicedev.github.client import GithubClient
    from alicedev.github.models import GithubItem, GithubKind

    class FakeGithub(GithubClient):
        async def fetch(self, ref):
            return GithubItem(kind=GithubKind.ISSUE, owner=ref.owner, repo=ref.repo, number=ref.number,
                              title="收藏夹崩溃", body="步骤…", labels=("bug",), state="open",
                              url=f"https://github.com/{ref.owner}/{ref.repo}/issues/{ref.number}")

    async def main() -> None:
        env = await make_env(tmp_path)
        env.dispatcher._github = FakeGithub()
        url = "https://github.com/TraderAlice/OpenAlice/issues/12"
        assert await env.dispatcher.handle(inbound(url))  # not addressed: the route still applies
        assert await env.texts() == ["已开始 %1 Issue #12 · 收藏夹崩溃"]
        row = await env.sessions.by_no(CHAT, 1)
        assert row.scenario == "github-issue" and row.input["github"]["labels"] == ["bug"]
        assert "收藏夹崩溃" in ingress(env.paseo.created[0]["prompt"])["text"]
        await env.close()

    run(main())
