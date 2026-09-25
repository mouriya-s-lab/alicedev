"""Status card fields and HTML render from the public status shape."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from alicedev.dsl.__main__ import _html  # noqa: E402
from alicedev.render import views  # noqa: E402

TEMPLATES = ROOT / "templates"


def make_status(
    *, revision: str = "0123456789abcdef", dsl_errors: list[dict[str, object]] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        generation=4,
        revision=revision,
        uptime_s=93_784,
        platforms=["telegram", "aiocqhttp"],
        commands=["状态", "会话"],
        scenarios=["requirement", "steward"],
        dsl_errors=[] if dsl_errors is None else dsl_errors,
        sessions={"active": 2, "queued": 1},
        agents={"active": 3, "closed": 1},
        outbox_pending=5,
    )


def test_status_card_builds_summary_and_renders_zero_errors() -> None:
    name, fields = views.status_card(make_status())

    assert name == "bot_status"
    assert fields == {
        "title": "机器人状态",
        "generation": 4,
        "revision": "0123456",
        "uptime": "1天 2小时 3分钟 4秒",
        "platforms": ["telegram", "aiocqhttp"],
        "commands_count": 2,
        "scenarios_count": 2,
        "sessions_active": 2,
        "sessions_queued": 1,
        "agents": [
            {"status": "active", "count": 3},
            {"status": "closed", "count": 1},
        ],
        "outbox_pending": 5,
        "dsl_error_count": 0,
        "dsl_errors": [],
        "dsl_errors_remaining": 0,
    }

    html = _html(TEMPLATES, name, fields)
    assert "revision 0123456" in html
    assert "1天 2小时 3分钟 4秒" in html
    assert "telegram" in html and "aiocqhttp" in html
    assert "进行中会话" in html and "待发送消息" in html
    assert "当前没有 DSL 错误" in html


def test_status_card_counts_errors_and_shows_only_first_three_escaped_messages() -> None:
    errors = [
        {"path": "commands/one.yaml", "line": 2, "message": "<bad command>"},
        {"path": "scenarios/two.yaml", "line": 3, "message": "second error"},
        {"path": "routes.yaml", "line": 4, "message": "third error"},
        {"path": "messages.yaml", "line": 5, "message": "fourth error"},
    ]
    name, fields = views.status_card(make_status(dsl_errors=errors))

    assert fields["dsl_error_count"] == 4
    assert [error["message"] for error in fields["dsl_errors"]] == [
        "<bad command>",
        "second error",
        "third error",
    ]
    assert fields["dsl_errors_remaining"] == 1

    html = _html(TEMPLATES, name, fields)
    assert "&lt;bad command&gt;" in html
    assert "<bad command>" not in html
    assert "fourth error" not in html
    assert "另有 1 条未展示" in html


def test_status_card_preserves_unknown_revision() -> None:
    _, fields = views.status_card(make_status(revision="unknown"))

    assert fields["revision"] == "unknown"
