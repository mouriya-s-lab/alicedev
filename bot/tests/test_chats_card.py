"""Chats card view-models include session and allowlisted chats."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from alicedev.dsl.__main__ import _html  # noqa: E402
from alicedev.dsl_api import chats_card  # noqa: E402

TEMPLATES = ROOT / "templates"


@dataclass(frozen=True)
class ChatFixture:
    chat_key: str
    name: str
    open_sessions: int
    last_activity_at: datetime | None



def known_chat(
    chat_key: str,
    name: str,
    open_sessions: int,
    last_activity_at: datetime | None,
) -> ChatFixture:
    return ChatFixture(
        chat_key=chat_key,
        name=name,
        open_sessions=open_sessions,
        last_activity_at=last_activity_at,
    )


def test_chats_card_empty() -> None:
    card, fields = chats_card([], ())

    assert card == "chats_list"
    assert fields["title"] == "群列表"
    assert fields["rows"] == []
    assert "没有已配置或有会话的群" in _html(TEMPLATES, card, fields)


def test_chats_card_merges_allowed_chats_without_overwriting_session_data() -> None:
    active_at = datetime(2026, 9, 24, 12, 34)
    card, fields = chats_card(
        [known_chat("telegram:group:1", "维护群", 3, active_at)],
        ("telegram:group:1", "telegram:group:2", "telegram:group:2"),
    )

    assert card == "chats_list"
    assert fields["rows"] == [
        {
            "chat_key": "telegram:group:1",
            "name": "维护群",
            "open_sessions": 3,
            "last_activity_at": "2026-09-24 12:34",
        },
        {
            "chat_key": "telegram:group:2",
            "name": "",
            "open_sessions": 0,
            "last_activity_at": "",
        },
    ]
    html = _html(TEMPLATES, card, fields)
    assert "维护群" in html and "telegram:group:1" in html
    assert "未结束会话 3" in html and "未结束会话 0" in html


def test_chats_card_sorts_activity_newest_first_and_never_active_last() -> None:
    card, fields = chats_card(
        [
            known_chat("telegram:older", "较早群", 1, datetime(2026, 9, 22, 8, 0)),
            known_chat("telegram:never", "无活动群", 0, None),
            known_chat("telegram:newer", "最近群", 2, datetime(2026, 9, 25, 9, 15)),
        ],
        (),
    )

    rows = fields["rows"]
    assert [row["chat_key"] for row in rows] == [
        "telegram:newer",
        "telegram:older",
        "telegram:never",
    ]
    assert [row["last_activity_at"] for row in rows] == [
        "2026-09-25 09:15",
        "2026-09-22 08:00",
        "",
    ]
    assert "最近活动 暂无" in _html(TEMPLATES, card, fields)
