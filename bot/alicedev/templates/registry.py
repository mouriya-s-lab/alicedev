"""Prompt template registry (Contract A / ARCHITECTURE §3).

A template is a Markdown file: YAML frontmatter (metadata + reply spec) and a
Jinja2 body that becomes the first-turn prompt. The directory is the registry —
adding a file adds a capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Union

import frontmatter
import jinja2


# --- Triggers ---------------------------------------------------------------

@dataclass(frozen=True)
class Command:
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Link:
    kind: str  # e.g. "github_issue" | "github_pr"


Trigger = Union[Command, Link]


# --- Reply spec -------------------------------------------------------------

@dataclass(frozen=True)
class ReplySpec:
    kinds: tuple[str, ...] = ("text",)
    image_templates: tuple[str, ...] = ()
    text_templates: tuple[str, ...] = ()
    stickers: tuple[str, ...] = ()
    max_text_chars: int = 600

    def allows(self, kind: str) -> bool:
        return kind in self.kinds


# --- Template ---------------------------------------------------------------

@dataclass(frozen=True)
class PromptTemplate:
    name: str
    trigger: Trigger
    description: str
    harness: str
    model: str
    effort: str
    cwd: str
    record: str | None
    reply: ReplySpec
    body: str


# --- Render variables -------------------------------------------------------

@dataclass(frozen=True)
class SenderVar:
    id: str
    name: str


@dataclass(frozen=True)
class ChatVar:
    key: str
    name: str


@dataclass(frozen=True)
class QuotedVar:
    sender: str
    text: str
    images: tuple[str, ...] = ()


@dataclass(frozen=True)
class GithubVar:
    kind: str
    owner: str
    repo: str
    number: int
    title: str
    body: str
    labels: tuple[str, ...]
    state: str
    url: str


@dataclass(frozen=True)
class TemplateVars:
    text: str = ""
    sender: SenderVar | None = None
    chat: ChatVar | None = None
    quoted: QuotedVar | None = None
    images: tuple[str, ...] = ()
    github: GithubVar | None = None
    session_ref: str = ""
    session_name: str = ""

    def as_context(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "sender": _dc_dict(self.sender),
            "chat": _dc_dict(self.chat),
            "quoted": _dc_dict(self.quoted),
            "images": list(self.images),
            "github": _dc_dict(self.github),
            "session_ref": self.session_ref,
            "session_name": self.session_name,
        }


def _dc_dict(obj: Any) -> Any:
    if obj is None:
        return None
    from dataclasses import asdict, is_dataclass

    if is_dataclass(obj):
        d = asdict(obj)
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()}
    return obj


# --- Registry ---------------------------------------------------------------

_REPLY_INSTRUCTIONS_HEADER = "\n\n---\n## 回复方式（alicedev）\n"


class TemplateRegistry:
    """Load ``*.md`` templates from a directory; resolve by command or link."""

    def __init__(self) -> None:
        self._by_name: dict[str, PromptTemplate] = {}
        self._by_command: dict[str, PromptTemplate] = {}
        self._by_link: dict[str, PromptTemplate] = {}
        self._jinja = jinja2.Environment(
            undefined=jinja2.ChainableUndefined,
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def load(self, root: Path) -> None:
        root = Path(root)
        if not root.exists():
            return
        for path in sorted(root.glob("*.md")):
            tpl = _parse_template(path)
            self._register(tpl)

    def _register(self, tpl: PromptTemplate) -> None:
        self._by_name[tpl.name] = tpl
        if isinstance(tpl.trigger, Command):
            self._by_command[tpl.trigger.name] = tpl
            for alias in tpl.trigger.aliases:
                self._by_command[alias] = tpl
        elif isinstance(tpl.trigger, Link):
            self._by_link[tpl.trigger.kind] = tpl

    def by_command(self, name: str) -> PromptTemplate | None:
        return self._by_command.get(name)

    def by_link(self, kind: str) -> PromptTemplate | None:
        return self._by_link.get(kind)

    def by_name(self, name: str) -> PromptTemplate | None:
        return self._by_name.get(name)

    def all(self) -> list[PromptTemplate]:
        return list(self._by_name.values())

    def render(self, tpl: PromptTemplate, vars: TemplateVars) -> str:
        rendered = self._jinja.from_string(tpl.body).render(**vars.as_context())
        return rendered.rstrip() + _reply_instructions(tpl.reply)


def _reply_instructions(reply: ReplySpec) -> str:
    lines = [_REPLY_INSTRUCTIONS_HEADER.strip(), ""]
    lines.append(
        "你必须通过 `chat_reply` 工具把答复发回群聊；在此之前不要结束本轮。"
    )
    lines.append(f"- 可用回复类型 kind：{', '.join(reply.kinds)}")
    if reply.image_templates:
        lines.append(f"- 图片模板 image_template：{', '.join(reply.image_templates)}")
    if reply.text_templates:
        lines.append(f"- 文字模板 text_template：{', '.join(reply.text_templates)}")
    if reply.stickers:
        lines.append(f"- 可用表情 sticker：{', '.join(reply.stickers)}")
    lines.append(
        f"- 纯文本超过 {reply.max_text_chars} 字会自动转为图片卡片；长内容请直接用 image_template。"
    )
    return "\n\n" + "\n".join(lines) + "\n"


def _parse_template(path: Path) -> PromptTemplate:
    post = frontmatter.load(str(path))
    meta: Mapping[str, Any] = post.metadata
    name = str(meta.get("name") or path.stem)
    trigger = _parse_trigger(meta, default_name=name)
    reply = _parse_reply(meta.get("reply") or {})
    return PromptTemplate(
        name=name,
        trigger=trigger,
        description=str(meta.get("description", "")),
        harness=str(meta.get("harness", "omp")),
        model=str(meta.get("model", "")),
        effort=str(meta.get("effort", "high")),
        cwd=str(meta.get("cwd", "") or ""),
        record=(str(meta["record"]) if meta.get("record") else None),
        reply=reply,
        body=post.content,
    )


def _parse_trigger(meta: Mapping[str, Any], *, default_name: str) -> Trigger:
    trig = meta.get("trigger") or {}
    aliases = tuple(str(a) for a in (meta.get("aliases") or []))
    if isinstance(trig, Mapping):
        if trig.get("command"):
            return Command(name=str(trig["command"]), aliases=aliases)
        if trig.get("link"):
            return Link(kind=str(trig["link"]))
    return Command(name=default_name, aliases=aliases)


def _parse_reply(spec: Mapping[str, Any]) -> ReplySpec:
    def _tuple(key: str) -> tuple[str, ...]:
        return tuple(str(v) for v in (spec.get(key) or []))

    return ReplySpec(
        kinds=_tuple("kinds") or ("text",),
        image_templates=_tuple("image_templates"),
        text_templates=_tuple("text_templates"),
        stickers=_tuple("stickers"),
        max_text_chars=int(spec.get("max_text_chars", 600)),
    )
