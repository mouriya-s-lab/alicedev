"""Jinja2 rendering for DSL templates (StrictUndefined) and the variable sets
each kind of template may reference (ARCHITECTURE §3.1, §3.5).

The loader rejects any template that references a variable outside its set;
the runtime must pass every variable of the set (``None`` when not
applicable) — :func:`context` fills the missing ones.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable, Mapping

import jinja2
import jinja2.meta

from alicedev.dsl.model import Tmpl

# Available to every command/route template.
BASE_VARS: frozenset[str] = frozenset({"sender", "chat", "quoted", "images"})

# Extra variables an action result may carry into its ``say`` / messages.yaml text.
#   session: {no, name, state, label}   reason: str   id: favorite id
#   error: parse error text   usage: command usage line   command: command name
#   count: number of items    link: issued url         data: transition data
RESULT_VARS: frozenset[str] = frozenset(
    {"session", "reason", "id", "error", "usage", "command", "count", "link", "data"}
)

# Routes additionally see the raw message text.
ROUTE_VARS: frozenset[str] = frozenset({"message"})

# messages.yaml: base + route + result vars (no command args).
MESSAGE_VARS: frozenset[str] = BASE_VARS | ROUTE_VARS | RESULT_VARS

# Scenario ``title``.
TITLE_VARS: frozenset[str] = frozenset(
    {"text", "sender", "chat", "quoted", "images", "github"}
)

# Scenario state prompts (ARCHITECTURE §3.4 prompt variables).
PROMPT_VARS: frozenset[str] = frozenset(
    {
        "text",
        "sender",
        "chat",
        "quoted",
        "images",
        "github",
        "session",
        "reports_dir",
        "repo",
        "data",
        "state",
        "next",
        "agent_ref",
    }
)


def _environment() -> jinja2.Environment:
    return jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


_ENV = _environment()


@lru_cache(maxsize=1024)
def _compile(source: str) -> jinja2.Template:
    return _ENV.from_string(source)


def render(t: Tmpl, ctx: Mapping[str, Any]) -> str:
    """Render a DSL template; undefined variables raise ``jinja2.UndefinedError``."""

    return _compile(t.source).render(**dict(ctx))


def context(names: Iterable[str], values: Mapping[str, Any]) -> dict[str, Any]:
    """Build a render context with every name in ``names`` present (default None)."""

    ctx: dict[str, Any] = {name: None for name in names}
    ctx.update(values)
    return ctx


class TemplateProblem(ValueError):
    """A template does not compile or references variables outside its set."""


def check(source: str, allowed: Iterable[str]) -> None:
    """Validate a template source at load time (syntax + variable set)."""

    try:
        ast = _ENV.parse(source)
    except jinja2.TemplateSyntaxError as exc:
        raise TemplateProblem(f"模板语法错误（第 {exc.lineno} 行）：{exc.message}") from exc
    unknown = sorted(jinja2.meta.find_undeclared_variables(ast) - set(allowed))
    if unknown:
        raise TemplateProblem("模板引用了不存在的变量：" + "、".join(unknown))
