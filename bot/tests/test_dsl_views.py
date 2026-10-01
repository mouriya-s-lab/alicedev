"""Cards render session and independent record identities without trusting markup."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from alicedev.domain import RequirementView, SessionView  # noqa: E402
from alicedev.dsl import load_registry  # noqa: E402
from alicedev.dsl.__main__ import _html, main  # noqa: E402
from alicedev.render import views  # noqa: E402

REG = load_registry(ROOT / "templates")
TEMPLATES = ROOT / "templates"
NOW = datetime(2026, 9, 23, 4, 0)


def session(no: int, *, name: str, current: bool = False) -> SessionView:
    return SessionView(
        session_id=no, chat_key="c", no=no, name=name, scenario="investigate", state="discussing",
        created_by="telegram:1", created_at=NOW, last_activity_at=NOW, is_current=current,
    )


def test_session_card_escapes_user_name() -> None:
    name, fields = views.session_card(
        session(4, name="<script>session & name</script>", current=True),
        REG.scenarios["investigate"],
    )
    html = _html(TEMPLATES, name, fields)
    assert "%4 &lt;script&gt;session &amp; name&lt;/script&gt;" in html
    assert "<script>session & name</script>" not in html


def test_session_list_renders_session_identity_and_escapes_names() -> None:
    rows = [
        session(1, name="<b>first & current</b>", current=True),
        session(2, name="<script>second</script>"),
    ]
    name, fields = views.session_list_card(
        rows, 1, 2, archived=True, command="会话列表", scenarios=REG.scenarios,
    )
    html = _html(TEMPLATES, name, fields)
    assert "%1" in html and "%2" in html
    assert "&lt;b&gt;first &amp; current&lt;/b&gt;" in html
    assert "&lt;script&gt;second&lt;/script&gt;" in html
    assert "<b>first & current</b>" not in html
    assert "<script>second</script>" not in html


def test_requirements_list_renders_independent_records_and_escapes_content() -> None:
    rows = [
        RequirementView(
            id=7, author_name="<b>Alice & Bob</b>", text="<script>record & text</script>",
            images=(), quoted=None, status="open", created_at=NOW,
        ),
        RequirementView(
            id=8, author_name="Carol", text="Second record",
            images=(), quoted=None, status="done", created_at=NOW,
        ),
    ]
    name, fields = views.requirements_list_card(rows, 1, 2, command="需求列表")
    html = _html(TEMPLATES, name, fields)
    assert "#7" in html and "#8" in html
    assert "%7" not in html and "%8" not in html
    assert "&lt;script&gt;record &amp; text&lt;/script&gt;" in html and "Second record" in html
    assert "&lt;b&gt;Alice &amp; Bob&lt;/b&gt;" in html and "Carol" in html
    assert "open" in html and "done" in html
    assert "<script>record & text</script>" not in html
    assert "<b>Alice & Bob</b>" not in html


def test_cli_render_escapes_json_content(tmp_path: Path) -> None:
    fields = tmp_path / "fields.json"
    fields.write_text(
        json.dumps({
            "title": "<script>title & text</script>",
            "session": "%3",
            "summary": "<b>summary & detail</b>",
            "changes": [{"before": "<i>old & value</i>", "after": "<i>new & value</i>"}],
            "notes": [],
            "pr": "",
        }),
        encoding="utf-8",
    )
    out = tmp_path / "explain.html"
    assert main([
        "--root", str(TEMPLATES), "render", "explain", "--fields", str(fields), "--html", str(out),
    ]) == 0
    html = out.read_text(encoding="utf-8")
    assert "&lt;script&gt;title &amp; text&lt;/script&gt;" in html
    assert "&lt;b&gt;summary &amp; detail&lt;/b&gt;" in html
    assert "&lt;i&gt;old &amp; value&lt;/i&gt;" in html
    assert "&lt;i&gt;new &amp; value&lt;/i&gt;" in html
    assert "<script>title & text</script>" not in html
    assert "<b>summary & detail</b>" not in html
    assert "<i>old & value</i>" not in html
    assert "<i>new & value</i>" not in html
