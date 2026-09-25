"""Session exchanges are reconstructed from real DuckDB message and outbox rows."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import rt_support  # noqa: F401 - puts bot/ on sys.path

from alicedev.store.db import Store
from alicedev.store.exchanges_repo import Exchange, ExchangesRepo

BASE = datetime(2026, 9, 25, 10, 0)


async def _session(store: Store, *, session_id: int = 1, chat: str = "chat-A",
                   no: int = 3, name: str = "需求") -> None:
    await store.execute(
        "INSERT INTO sessions (session_id, chat_key, no, scenario, name, created_by, input, "
        "state, data) VALUES (?, ?, ?, 'requirement', ?, 'user', '{}', 'discussing', '{}')",
        (session_id, chat, no, name),
    )


async def _message(store: Store, ref: str, second: int, content: str | None, *,
                   session_id: int = 1, chat: str = "chat-A", sender_name: str | None = "阿青",
                   sender_key: str | None = "tg:42") -> None:
    await store.execute(
        "INSERT INTO messages (msg_ref, chat_key, sender_key, sender_name, content, text, "
        "session_id, created_at) VALUES (?, ?, ?, ?, ?, 'rendered agent prompt', ?, ?)",
        (ref, chat, sender_key, sender_name, content, session_id,
         BASE + timedelta(seconds=second)),
    )


async def _reply(store: Store, ref: str, second: int, reply: dict[str, Any] | None, *,
                 session_id: int = 1, chat: str = "chat-A", agent_ref: str | None = "a_123",
                 state: str = "sent", published: dict[str, Any] | None = None) -> None:
    payload = {"type": "ai", "reply": reply, "marker": "%3 需求"} if reply else {
        "type": "transition_only"
    }
    if published is not None:
        payload["published"] = published
    seq = int((await store.fetch_one("SELECT COALESCE(MAX(seq), 0) + 1 FROM outbox"))[0])
    await store.execute(
        "INSERT INTO outbox (reply_id, seq, chat_key, session_id, agent_ref, msgs, "
        "payload, payload_sha256, state, created_at) VALUES (?, ?, ?, ?, ?, '[]', ?, 'digest', ?, ?)",
        (ref, seq, chat, session_id, agent_ref, json.dumps(payload, ensure_ascii=False), state,
         BASE + timedelta(seconds=second)),
    )


def test_recent_merges_last_n_in_time_order_and_excludes_non_exchanges(tmp_path: Path) -> None:
    async def run() -> None:
        store = Store(tmp_path / "recent.duckdb")
        await store.open()
        try:
            await _session(store)
            await _session(store, session_id=2, chat="chat-B", no=1, name="别的会话")
            await _message(store, "m_old", 1, "群友原文", sender_name=None)
            await _reply(store, "reply-old", 2, {"kind": "text", "text": "第一条回复"})
            await _message(store, "m_internal", 3, None)
            await _reply(store, "reply-failed", 4, {"kind": "text", "text": "未发出"}, state="failed")
            await _reply(store, "reply-bot", 5, {"kind": "text", "text": "程序回复"}, agent_ref=None)
            await _reply(store, "reply-transition", 6, None)
            await _message(store, "m_empty", 7, "")
            await _reply(store, "reply-queued", 8, {"kind": "text", "text": "待发"}, state="queued")
            await _message(store, "m_same", 9, "同时的人")
            await _reply(store, "reply-same", 9, {"kind": "text", "text": "同时的 AI"})
            await _message(store, "m_other", 10, "别的群", session_id=2, chat="chat-B")
            await _reply(store, "reply-other", 11, {"kind": "text", "text": "别的会话"},
                         session_id=2, chat="chat-B")

            repo = ExchangesRepo(store)
            assert [item.id for item in await repo.recent(1)] == [
                "m_old", "reply-old", "m_empty", "reply-queued", "m_same", "reply-same"
            ]
            assert [item.id for item in await repo.recent(1, limit=3)] == [
                "reply-queued", "m_same", "reply-same"
            ]
            assert await repo.recent(1, limit=0) == []
            assert [item.id for item in await repo.recent(2)] == ["m_other", "reply-other"]
            assert await repo.recent(999) == []
            assert (await repo.get("m_old")) == Exchange(
                id="m_old", kind="human", session_id=1, chat_key="chat-A",
                author_key="tg:42", author_name="tg:42", text="群友原文", images=(),
                at=BASE + timedelta(seconds=1),
            )
            assert (await repo.get("reply-old")) == Exchange(
                id="reply-old", kind="ai", session_id=1, chat_key="chat-A",
                author_key=None, author_name="%3 需求", text="第一条回复", images=(),
                at=BASE + timedelta(seconds=2),
            )
            assert (await repo.get("m_empty")).author_name == "阿青"
            for excluded in ("m_internal", "reply-failed", "reply-bot", "reply-transition",
                             "m_unknown", "reply-unknown"):
                assert await repo.get(excluded) is None
        finally:
            await store.close()

    asyncio.run(run())


def test_reply_payload_kinds_and_published_file_links(tmp_path: Path) -> None:
    async def run() -> None:
        store = Store(tmp_path / "payloads.duckdb")
        await store.open()
        try:
            await _session(store, name="新名称")
            await _reply(store, "text", 1, {"kind": "text", "text": "纯文本"})
            await _reply(store, "text_template", 2, {"kind": "text_template", "template": "x",
                                                      "fields": {"first": "甲", "second": "乙"}})
            await _reply(store, "image_template", 3, {"kind": "image_template", "template": "x",
                                                       "fields": {"title": "标题", "count": 4,
                                                                  "text": "正文"}})
            await _reply(store, "image", 4, {"kind": "image", "caption": "两张图",
                                             "paths": ["/reports/a.png", "/reports/b.png"]})
            await _reply(store, "image_no_caption", 5,
                         {"kind": "image", "paths": ["/reports/c.png"]})
            await _reply(store, "sticker", 6, {"kind": "sticker", "sticker": "smile"})
            await _reply(store, "file_url", 7,
                         {"kind": "file", "path": "/reports/private/draft.md", "caption": "报告"},
                         published={"basename": "draft.md", "path": "/reports/_published/r1/draft.md",
                                    "is_markdown": True, "url": "https://example.test/r/r1/draft.md"})
            await _reply(store, "file_basename", 8,
                         {"kind": "file", "path": "/reports/private/source.pdf", "caption": "附件"},
                         published={"basename": "delivered.pdf",
                                    "path": "/reports/_published/r2/delivered.pdf",
                                    "is_markdown": False, "url": None})
            await _reply(store, "file_unpublished", 9,
                         {"kind": "file", "path": "/reports/private/original.txt"})
            repo = ExchangesRepo(store)
            exchanges = {item.id: item for item in await repo.recent(1)}
            assert {key: (value.text, value.images) for key, value in exchanges.items()} == {
                "text": ("纯文本", ()),
                "text_template": ("甲\n乙", ()),
                "image_template": ("标题\n正文", ()),
                "image": ("两张图", ("/reports/a.png", "/reports/b.png")),
                "image_no_caption": ("", ("/reports/c.png",)),
                "sticker": ("[表情]", ()),
                "file_url": ("报告\nhttps://example.test/r/r1/draft.md", ()),
                "file_basename": ("附件\ndelivered.pdf", ()),
                "file_unpublished": ("original.txt", ()),
            }
            assert all(item.author_name == "%3 新名称" for item in exchanges.values())
            assert await repo.get("file_url") == exchanges["file_url"]
        finally:
            await store.close()

    asyncio.run(run())
