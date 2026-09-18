"""Read-only published report serving with safe Markdown rendering."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
import re

from markdown_it import MarkdownIt
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from mdit_py_plugins.texmath import texmath_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound


_REPORT_ID_PATTERN = re.compile(r"^r_[a-z2-7]{26}$")
_SAFE_BASENAME_PATTERN = re.compile(r"^[^/\\\x00-\x1f\x7f]+$")
_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z0-9_+.#-]{1,64}$")

# Keep this list deliberately small. Reports may link to sibling images and
# plain attachments, but they must not turn the gateway into a static file host.
_ALLOWED_EXTENSIONS: dict[str, str] = {
    ".bmp": "image/bmp",
    ".csv": "text/csv; charset=utf-8",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".json": "application/json; charset=utf-8",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".webp": "image/webp",
}


@dataclass(frozen=True, slots=True)
class ReportAsset:
    path: Path
    content_type: str


class ReportRenderer:
    """Markdown renderer configured once and used for every published report."""

    def __init__(self) -> None:
        markdown = MarkdownIt("commonmark", {"html": False, "breaks": True})
        markdown.enable(["table", "strikethrough"])
        markdown.use(deflist_plugin)
        markdown.use(footnote_plugin)
        markdown.use(front_matter_plugin)
        markdown.use(tasklists_plugin)
        markdown.use(texmath_plugin)
        markdown.renderer.rules["fence"] = self._render_fence
        self._markdown = markdown

    def render(self, source: str, *, title: str) -> str:
        body = self._markdown.render(source)
        safe_title = escape(title)
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <link rel="stylesheet" href="/_alicedev/static/pygments.css">
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ max-width: 1040px; margin: 0 auto; padding: 2rem 1.25rem 4rem; line-height: 1.65; }}
    .report-meta {{ color: #667085; font-size: .9rem; margin-bottom: 2rem; }}
    pre {{ overflow-x: auto; padding: 1rem; border-radius: .5rem; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #98a2b3; padding: .45rem .65rem; text-align: left; }}
    img {{ max-width: 100%; height: auto; }}
    .task-list-item {{ list-style: none; }}
    .task-list-item-checkbox {{ margin-right: .45rem; }}
    .mermaid {{ overflow-x: auto; margin: 1.5rem 0; }}
  </style>
</head>
<body>
  <div class="report-meta">alicedev 调查报告 · 只读公开链接</div>
  <main>{body}</main>
  <script src="/_alicedev/static/mermaid.min.js"></script>
  <script>mermaid.initialize({{ startOnLoad: true, securityLevel: 'strict' }});</script>
</body>
</html>
"""


    @staticmethod
    def _render_fence(
        tokens: list[object],
        index: int,
        options: object,
        env: object,
    ) -> str:
        del options, env
        token = tokens[index]
        info = getattr(token, "info", "").strip()
        language = info.split(maxsplit=1)[0] if info else ""
        content = getattr(token, "content", "")
        if language.lower() == "mermaid":
            return f'<div class="mermaid">{escape(content)}</div>\n'
        if language and _LANGUAGE_PATTERN.fullmatch(language):
            try:
                lexer = get_lexer_by_name(language, stripall=False)
            except ClassNotFound:
                lexer = TextLexer(stripall=False)
            highlighted = highlight(
                content,
                lexer,
                HtmlFormatter(nowrap=True, cssclass="highlight"),
            )
            class_name = f" class=\"language-{escape(language, quote=True)}\""
            return f"<div class=\"highlight\"><pre><code{class_name}>{highlighted}</code></pre></div>\n"
        return f"<pre><code>{escape(content)}</code></pre>\n"


def resolve_report_asset(root: Path, report_id: str, basename: str) -> ReportAsset | None:
    """Resolve one regular published file without following an escape symlink."""

    if not _REPORT_ID_PATTERN.fullmatch(report_id):
        return None
    if not _SAFE_BASENAME_PATTERN.fullmatch(basename) or ".." in basename:
        return None
    suffix = Path(basename).suffix.lower()
    if suffix == ".md":
        content_type = "text/html; charset=utf-8"
    else:
        content_type = _ALLOWED_EXTENSIONS.get(suffix)
        if content_type is None:
            return None

    try:
        root_real = root.resolve(strict=True)
        report_dir_path = root_real / report_id
        if report_dir_path.is_symlink():
            return None
        report_dir = report_dir_path.resolve(strict=True)
        report_dir.relative_to(root_real)
        candidate_path = report_dir / basename
        if candidate_path.is_symlink():
            return None
        candidate = candidate_path.resolve(strict=True)
        candidate.relative_to(report_dir)
    except (FileNotFoundError, OSError, ValueError):
        return None

    if not candidate.is_file():
        return None
    return ReportAsset(path=candidate, content_type=content_type)


def is_report_id(value: str) -> bool:
    return bool(_REPORT_ID_PATTERN.fullmatch(value))
