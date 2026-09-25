"""Card view-models build from the DSL and every card template renders."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from alicedev.domain import FavoriteView, SessionView  # noqa: E402
from alicedev.dsl import load_registry  # noqa: E402
from alicedev.dsl.__main__ import _html, main  # noqa: E402
from alicedev.render import views  # noqa: E402

REG = load_registry(ROOT / "templates")
TEMPLATES = ROOT / "templates"
NOW = datetime(2026, 9, 23, 4, 0)


def session(no: int, state: str = "discussing", scenario: str = "requirement", current: bool = False) -> SessionView:
    return SessionView(
        session_id=no, chat_key="c", no=no, name=f"需求 · {no}", scenario=scenario, state=state,
        created_by="telegram:1", created_at=NOW, last_activity_at=NOW, is_current=current,
    )


def test_help_card_groups_ai_and_program() -> None:
    name, fields = views.help_card(REG, session(3, current=True), REG.scenarios["requirement"])
    assert name == "help"
    ai, program = fields["groups"]
    ai_usages = [e["usage"] for e in ai["entries"]]
    assert "/继续 [%会话] <内容>" in ai_usages
    assert "/管家 <内容>" in ai_usages and len(ai_usages) == 6
    program_usages = [e["usage"] for e in program["entries"]]
    assert "/升级bot approve [%会话]" in program_usages
    assert "/会话列表 全部 [页]" in program_usages
    assert fields["current"]["label"] == "%3"
    assert "<title>" not in _html(TEMPLATES, name, fields)  # renders


def test_command_card_includes_flow_for_new_session_commands() -> None:
    cmd = REG.commands["升级bot"]
    name, fields = views.command_card(cmd, views.command_scenario(REG, cmd))
    assert name == "command"
    assert [s["usage"] for s in fields["subcommands"]] == ["/升级bot approve [%会话]", "/升级bot reject [%会话]"]
    states = {s["name"]: s for s in fields["flow"]["states"]}
    assert {"queued", "working", "awaiting_approval", "deploying"} <= set(states)
    assert any(e["label"] == "/approve" for e in states["awaiting_approval"]["edges"])
    html = _html(TEMPLATES, name, fields)
    assert "awaiting_approval" in html and "/升级bot approve" in html


def test_session_and_list_cards_render() -> None:
    scenario = REG.scenarios["upgrade-bot"]
    name, fields = views.session_card(session(4, "awaiting_approval", "upgrade-bot", True), scenario)
    html = _html(TEMPLATES, name, fields)
    assert "%4" in html and "current-state" in html

    rows = [session(1, current=True), session(2, "archived")]
    for card in ("session_list", "requirements_list"):
        name, fields = views.SESSION_LIST_CARDS[card](rows, 1, 2, archived=True, command="会话列表", scenarios=REG.scenarios)
        html = _html(TEMPLATES, name, fields)
        assert "%1" in html and "第 1/2 页" in html

    fav = FavoriteView(id=7, author_name="a", saver_name="b", text="t", images=(), created_at=NOW)
    name, fields = views.favorites_list_card([fav], 1, 1, command="收藏夹")
    assert "#7" in _html(TEMPLATES, name, fields)


def test_cli_check_and_card_html(tmp_path: Path, capsys) -> None:
    assert main(["--root", str(TEMPLATES), "check"]) == 0
    out = tmp_path / "card.html"
    assert main(["--root", str(TEMPLATES), "card", "继续", "--html", str(out)]) == 0
    assert "/继续 [%会话] &lt;内容&gt;" in out.read_text(encoding="utf-8")
    out2 = tmp_path / "scenario.html"
    assert main(["--root", str(TEMPLATES), "card", "upgrade-bot", "--html", str(out2)]) == 0
    fields = tmp_path / "f.json"
    fields.write_text('{"title":"t","session":"%3","summary":"s","changes":[{"before":"a","after":"b"}],"notes":[],"pr":"p"}', encoding="utf-8")
    out3 = tmp_path / "explain.html"
    assert main(["--root", str(TEMPLATES), "render", "explain", "--fields", str(fields), "--html", str(out3)]) == 0
    assert "之后" in out3.read_text(encoding="utf-8")
