"""Command/route dispatch and the closed action set (ARCHITECTURE §3.2, §3.3, §9).

Middleware order: whitelist → parse (DSL) → permission → action → reply by
result key. Every reply goes through the outbox (§6). Permission denials are
logged and never answered (avoid spam).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Mapping

from alicedev import dsl_api
from alicedev.actions.inbound import Inbound, exact_github_url, from_event
from alicedev.dsl.invocation import FromEvent, SessionArg
from alicedev.dsl.model import (
    Action,
    Command,
    FavoriteAction,
    GithubAction,
    HelpAction,
    HumanAction,
    ListAction,
    ListSource,
    Permission,
    Registry,
    RouteWhen,
    SendAction,
    SessionArchiveAction,
    SessionRenameAction,
    SessionShowAction,
    SessionSwitchAction,
    ShareAction,
    StartAction,
    Tmpl,
)
from alicedev.dsl.render import BASE_VARS, MESSAGE_VARS, RESULT_VARS, ROUTE_VARS
from alicedev.domain import FavoriteView
from alicedev.github.client import GithubClient, GithubFetchError
from alicedev.images import ImageDownloadError, download_quoted_images
from alicedev.render.pagination import clamp_page, page_count
from alicedev.scheduler.engine import StartOutcome, StartRequest

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from alicedev.config import PluginConfig
    from alicedev.outbox.service import Outbox
    from alicedev.scheduler.engine import Scheduler
    from alicedev.store.db import Store
    from alicedev.store.favorites_repo import FavoritesRepo
    from alicedev.store.sessions_repo import SessionRow, SessionsRepo

_LOG = logging.getLogger("alicedev.actions")

_SILENT = "__silent__"


@dataclass
class Outcome:
    result: str
    vars: dict[str, Any] = field(default_factory=dict)
    card: tuple[str, dict[str, Any]] | None = None
    session_id: int | None = None


@dataclass
class Invocation:
    """What an action needs about the message that triggered it."""

    inbound: Inbound
    args: Mapping[str, Any]
    command: Command | None  # None for routes
    path: tuple[str, ...] = ()  # command words as typed, e.g. ("会话列表", "全部")
    # Session args resolved by the action, exposed to ``say`` as ``session``-shaped dicts.
    resolved: dict[str, dict[str, Any] | None] = field(default_factory=dict)


def _session_ctx(row: "SessionRow | None") -> dict[str, Any] | None:
    if row is None:
        return None
    return {"no": row.no, "name": row.name, "state": row.state, "label": f"%{row.no}",
            "scenario": row.scenario}


class Dispatcher:
    def __init__(
        self,
        *,
        config: "PluginConfig",
        store: "Store",
        sessions: "SessionsRepo",
        favorites: "FavoritesRepo",
        scheduler: "Scheduler",
        outbox: "Outbox",
        github: "GithubClient | None",
        registry: Callable[[], Registry],
    ) -> None:
        self._config = config
        self._store = store
        self._sessions = sessions
        self._favorites = favorites
        self._scheduler = scheduler
        self._outbox = outbox
        self._github = github
        self._registry = registry

    # --- entry --------------------------------------------------------------------

    async def handle_event(self, event: "AstrMessageEvent") -> bool:
        inbound = from_event(event, admin_users=self._config.admin_users)
        handled = await self.handle(inbound)
        if handled:
            try:
                event.stop_event()
            except Exception:  # noqa: BLE001
                pass
        return handled

    async def handle(self, inbound: Inbound) -> bool:
        if not self._config.chat_allowed(inbound.chat_key):
            _LOG.info("chat not allowed: %s", inbound.chat_key)
            return False
        registry = self._registry()
        if inbound.is_slash:
            return await self._handle_command(inbound, registry)
        return await self._handle_route(inbound, registry)

    async def _handle_command(self, inbound: Inbound, registry: Registry) -> bool:
        matched = dsl_api.match(registry, inbound.text)
        if matched is None:
            if not inbound.addressed:
                return False
            token = inbound.text.lstrip()[1:].split(None, 1)
            await self._system(inbound, "unknown_command", command=token[0] if token else "")
            return True
        command = matched.command
        parsed = dsl_api.parse_args(matched)
        if isinstance(parsed, dsl_api.ArgsError):
            await self._system(inbound, "usage_error", usage=command.usage, error=parsed.message)
            return True
        if command.permission is Permission.ADMIN and not inbound.is_admin:
            _LOG.info("permission denied: %s /%s", inbound.user_key, command.name)
            return True
        inv = Invocation(inbound=inbound, args=parsed.values, command=command, path=matched.path)
        await self._run(inv, command.do, command.say, permission=command.permission)
        return True

    async def _handle_route(self, inbound: Inbound, registry: Registry) -> bool:
        text = inbound.text.strip()
        if not text:
            return False
        for route in registry.routes:
            match route.when:
                case RouteWhen.GITHUB_LINK:
                    applies = exact_github_url(text)
                case RouteWhen.ADDRESSED:
                    applies = inbound.addressed
            if applies:
                inv = Invocation(inbound=inbound, args={"message": text}, command=None)
                await self._run(inv, route.do, route.say, permission=Permission.ALL)
                return True
        return False

    async def _run(
        self, inv: Invocation, action: Action, say: Mapping[str, Tmpl], *, permission: Permission
    ) -> None:
        try:
            outcome = await self.execute(inv, action, permission)
        except Exception as exc:  # noqa: BLE001 - a bad action must not crash the adapter
            _LOG.exception("action %s failed", type(action).__name__)
            await self._system(inv.inbound, "internal_error", reason=str(exc) or None)
            return
        if outcome.result == _SILENT:
            return
        if outcome.card is not None:
            card, fields = outcome.card
            await self._outbox.enqueue(inv.inbound.chat_key,
                                       {"type": "card", "card": card, "fields": fields},
                                       session_id=outcome.session_id)
            return
        tmpl = say.get(outcome.result) or self._registry().messages.get(
            dsl_api.message_key(action, outcome.result)
        )
        if tmpl is None:
            _LOG.warning("no reply text for %s.%s", type(action).__name__, outcome.result)
            return
        try:
            text = dsl_api.render(tmpl, self._ctx(inv, outcome.vars))
        except Exception as exc:  # noqa: BLE001 - e.g. a say template touching a None var
            _LOG.exception("reply render failed for %s.%s", type(action).__name__, outcome.result)
            await self._system(inv.inbound, "internal_error", reason=str(exc) or None)
            return
        await self._outbox.enqueue_text(inv.inbound.chat_key, text, session_id=outcome.session_id)

    # --- template contexts ------------------------------------------------------------

    def _arg_ctx(self, inv: Invocation) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, value in inv.args.items():
            match value:
                case FromEvent.MENTIONS:
                    out[name] = [{"id": m.user_key, "name": m.name} for m in inv.inbound.mentions]
                case FromEvent.QUOTED:
                    out[name] = inv.inbound.base_vars()["quoted"]
                case SessionArg():
                    out[name] = inv.resolved.get(name)
                case _:
                    out[name] = value
        return out

    def _ctx(self, inv: Invocation, extra: Mapping[str, Any]) -> dict[str, Any]:
        ctx: dict[str, Any] = {n: None for n in MESSAGE_VARS | BASE_VARS | RESULT_VARS | ROUTE_VARS}
        ctx.update(inv.inbound.base_vars())
        ctx.update(self._arg_ctx(inv))
        ctx.update(extra)
        return ctx

    async def _system(self, inbound: Inbound, key: str, **extra: Any) -> None:
        tmpl = self._registry().messages.get(key)
        if tmpl is None:
            _LOG.warning("messages.yaml has no %r", key)
            return
        ctx: dict[str, Any] = {n: None for n in MESSAGE_VARS}
        ctx.update(inbound.base_vars())
        ctx.update(extra)
        await self._outbox.enqueue_text(inbound.chat_key, dsl_api.render(tmpl, ctx))

    # --- session resolution ---------------------------------------------------------------

    async def resolve_session(self, inbound: Inbound, no: int | None) -> "SessionRow | None":
        """Explicit %n > quoted bot message's session > this chat's current session."""
        if no is not None:
            return await self._sessions.by_no(inbound.chat_key, no)
        if inbound.quoted is not None:
            marker = inbound.quoted.session_marker
            if marker is not None:
                row = await self._sessions.by_no(inbound.chat_key, marker)
                if row is not None:
                    return row
            if inbound.quoted.platform_message_id:
                hit = await self._store.fetch_one(
                    "SELECT session_id FROM outbound WHERE platform_message_id = ? AND chat_key = ?",
                    (inbound.quoted.platform_message_id, inbound.chat_key),
                )
                if hit is not None and hit[0] is not None:
                    row = await self._sessions.get(int(hit[0]))
                    if row is not None:
                        return row
        return await self._sessions.current(inbound.chat_key)

    async def _session_arg(self, inv: Invocation, arg: str | None) -> "SessionRow | None":
        value = inv.args.get(arg) if arg else None
        no = value.no if isinstance(value, SessionArg) else None
        row = await self.resolve_session(inv.inbound, no)
        if arg:
            inv.resolved[arg] = _session_ctx(row)
        return row

    def _render(self, inv: Invocation, tmpl: Tmpl) -> str:
        return dsl_api.render(tmpl, self._ctx(inv, {})).strip()

    # --- actions ----------------------------------------------------------------------

    async def execute(self, inv: Invocation, action: Action, permission: Permission) -> Outcome:
        inbound = inv.inbound
        match action:
            case StartAction(scenario=scenario, text=text):
                return await self._start(inv, scenario, self._render(inv, text), github=None)
            case GithubAction(ref=ref, scenarios=scenarios):
                return await self._github_start(inv, self._render(inv, ref), scenarios)
            case SendAction(session=arg, text=text):
                row = await self._session_arg(inv, arg)
                if row is None:
                    return Outcome("not_found")
                result = await self._scheduler.send(
                    row, text=self._render(inv, text),
                    platform_message_id=inbound.platform_message_id, sender_key=inbound.user_key,
                )
                if result == "duplicate":
                    return Outcome(_SILENT)
                return Outcome(result, {"session": _session_ctx(row)}, session_id=row.session_id)
            case SessionShowAction(session=arg):
                row = await self._session_arg(inv, arg)
                if row is None:
                    return Outcome("not_found")
                view = await self._sessions.view(row)
                card = dsl_api.session_card(view, self._scheduler.scenario(row.scenario))
                return Outcome("ok", card=card, session_id=row.session_id)
            case SessionSwitchAction(session=arg):
                row = await self._session_arg(inv, arg)
                if row is None:
                    return Outcome("not_found")
                await self._sessions.set_current(inbound.chat_key, row.session_id)
                return Outcome("ok", {"session": _session_ctx(row)}, session_id=row.session_id)
            case SessionRenameAction(session=arg, name=name):
                row = await self._session_arg(inv, arg)
                if row is None:
                    return Outcome("not_found")
                if not self._owner_ok(inbound, row, permission):
                    return Outcome(_SILENT)
                await self._sessions.rename(row.session_id, self._render(inv, name))
                renamed = await self._sessions.get(row.session_id)
                return Outcome("ok", {"session": _session_ctx(renamed)}, session_id=row.session_id)
            case SessionArchiveAction(session=arg):
                row = await self._session_arg(inv, arg)
                if row is None:
                    return Outcome("not_found")
                if not self._owner_ok(inbound, row, permission):
                    return Outcome(_SILENT)
                await self._scheduler.archive(row)
                return Outcome("ok", {"session": _session_ctx(row)}, session_id=row.session_id)
            case HumanAction(session=arg, command=command):
                row = await self._session_arg(inv, arg)
                if row is None:
                    return Outcome("not_found")
                if not self._owner_ok(inbound, row, permission):
                    return Outcome(_SILENT)
                result = await self._scheduler.human(row, command)
                after = await self._sessions.get(row.session_id)
                return Outcome(result, {"session": _session_ctx(after)}, session_id=row.session_id)
            case ShareAction(session=arg, to=to):
                return await self._share(inv, arg, to)
            case FavoriteAction():
                return await self._favorite(inv)
            case ListAction():
                return await self._list(inv, action)
            case HelpAction(command=arg):
                return await self._help(inv, arg)

    def _owner_ok(self, inbound: Inbound, row: "SessionRow", permission: Permission) -> bool:
        if permission is not Permission.OWNER:
            return True
        ok = inbound.is_admin or row.created_by == inbound.user_key
        if not ok:
            _LOG.info("owner permission denied: %s on session %s", inbound.user_key, row.session_id)
        return ok

    async def _start(
        self, inv: Invocation, scenario: str, text: str, *, github: Mapping[str, Any] | None
    ) -> Outcome:
        inbound = inv.inbound
        prompt_input = {**inbound.base_vars(), "text": text, "github": github}
        result = await self._scheduler.start(
            StartRequest(
                chat_key=inbound.chat_key, user_key=inbound.user_key, scenario=scenario,
                input=prompt_input, platform_message_id=inbound.platform_message_id,
            )
        )
        if result.outcome in (StartOutcome.DUPLICATE, StartOutcome.NOTIFIED):
            return Outcome(_SILENT)
        if result.session is None:  # unknown scenario: nothing was created
            await self._system(inbound, "internal_error", reason=result.reason or None)
            return Outcome(_SILENT)
        return Outcome(
            result.outcome.value,
            {"session": _session_ctx(result.session), "reason": result.reason or None},
            session_id=result.session.session_id,
        )

    async def _github_start(self, inv: Invocation, ref_text: str, scenarios: Mapping[str, str]) -> Outcome:
        if self._github is None:
            return Outcome("fetch_failed", {"reason": "GitHub 预取未配置"})
        ref = self._github.try_parse(ref_text)
        if ref is None:
            return Outcome("fetch_failed", {"reason": f"无法识别的链接：{ref_text}"})
        try:
            item = await self._github.fetch(ref)
        except GithubFetchError as exc:
            return Outcome("fetch_failed", {"reason": str(exc) or None})
        scenario = scenarios.get(item.kind.resource_name)
        if not scenario:
            return Outcome("fetch_failed", {"reason": "没有对应的场景"})
        outcome = await self._start(inv, scenario, ref_text, github=GithubClient.to_prompt_var(item))
        if outcome.result == StartOutcome.QUEUED.value:  # github has no "queued" reply
            return Outcome("created", outcome.vars, session_id=outcome.session_id)
        return outcome

    async def _share(self, inv: Invocation, arg: str | None, to: str | None) -> Outcome:
        inbound = inv.inbound
        row = await self._session_arg(inv, arg)
        if row is None:
            return Outcome("not_found")
        recipients = [(m.user_key, m.platform_id, m.name) for m in inbound.mentions] if (
            to and inv.args.get(to) is FromEvent.MENTIONS and inbound.mentions
        ) else [(inbound.user_key, inbound.sender_id, inbound.sender_name)]
        sent = 0
        for user_key, platform_id, name in recipients:
            url = await self._scheduler.issue_link(row, user_key=user_key, issued_by=inbound.user_key)
            if url is None:
                return Outcome("not_ready", {"session": _session_ctx(row)}, session_id=row.session_id)
            await self._outbox.enqueue(
                inbound.chat_key,
                {"type": "at_text", "platform_id": platform_id, "name": name, "text": f" {url}"},
                session_id=row.session_id,
            )
            sent += 1
        return Outcome("ok", {"session": _session_ctx(row), "count": sent}, session_id=row.session_id)

    async def _favorite(self, inv: Invocation) -> Outcome:
        quoted = inv.inbound.quoted
        if quoted is None:
            command = inv.command
            await self._system(inv.inbound, "usage_error",
                               usage=command.usage if command else "", error="请先引用一条消息")
            return Outcome(_SILENT)
        try:
            images = await download_quoted_images(quoted.image_urls, self._config.images_root)
        except ImageDownloadError:
            return Outcome("image_failed")
        record_id = await self._favorites.insert(
            chat_key=inv.inbound.chat_key, saver_key=inv.inbound.user_key,
            saver_name=inv.inbound.sender_name, author_key=quoted.sender_key,
            author_name=quoted.sender_name, text=quoted.text, images=[str(p) for p in images],
            platform_message_id=quoted.platform_message_id,
        )
        return Outcome("ok", {"id": record_id})

    async def _list(self, inv: Invocation, action: ListAction) -> Outcome:
        chat = inv.inbound.chat_key
        page_value = inv.args.get(action.page) if action.page else None
        page = int(page_value) if isinstance(page_value, int) else 1
        command = " ".join(inv.path)  # the page hint repeats the full command, subcommand included
        match action.source:
            case ListSource.SESSIONS:
                rows, total = await self._sessions.page(
                    chat, page=page, scenario=action.scenario, include_ended=action.archived,
                    ended_states=self._scheduler.ended_states(),
                )
                pages = page_count(total)
                if total == 0:
                    return Outcome("empty", {"count": 0})
                if page > pages:
                    rows, total = await self._sessions.page(
                        chat, page=clamp_page(page, pages), scenario=action.scenario,
                        include_ended=action.archived, ended_states=self._scheduler.ended_states(),
                    )
                    page = clamp_page(page, pages)
                views = [await self._sessions.view(r) for r in rows]
                card = dsl_api.session_list_card(
                    action.card, views, page, pages, archived=action.archived, command=command,
                    scenarios=self._registry().scenarios,
                )
                return Outcome("ok", card=card)
            case ListSource.FAVORITES:
                fav_page = await self._favorites.list_page(chat, page)
                if fav_page.total == 0:
                    return Outcome("empty", {"count": 0})
                rows = [
                    FavoriteView(id=r.id, author_name=r.author_name, saver_name=r.saver_name,
                                 text=r.text, images=r.images, created_at=r.created_at)
                    for r in fav_page.items
                ]
                card = dsl_api.favorites_list_card(rows, fav_page.page, fav_page.pages,
                                                   command=command)
                return Outcome("ok", card=card)

    async def _help(self, inv: Invocation, arg: str | None) -> Outcome:
        registry = self._registry()
        name = inv.args.get(arg) if arg else None
        if isinstance(name, str) and name.strip():
            command = registry.commands.get(name.strip().lstrip("/／"))
            if command is None:
                return Outcome("not_found", {"command": name})
            return Outcome("ok", card=dsl_api.command_card(registry, command))
        current = await self._sessions.current(inv.inbound.chat_key)
        view = await self._sessions.view(current) if current else None
        scenario = self._scheduler.scenario(current.scenario) if current else None
        return Outcome("ok", card=dsl_api.help_card(registry, view, scenario))
