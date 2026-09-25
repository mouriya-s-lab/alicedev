"""Command/route dispatch and the closed action set (ARCHITECTURE §3.2, §3.3, §6.1, §9).

Middleware order: whitelist → parse (DSL) → permission → action → reply by
result key. Every reply goes through the outbox (§6). Permission denials are
logged and never answered (avoid spam).

Agents reach the same commands through ``run_agent`` (``POST /v1/commands``):
same parsing, permission and actions, acting for the session creator in the
target chat; the result comes back as JSON instead of a chat reply (§6.1).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping

from alicedev import dsl_api
from alicedev.actions.inbound import Inbound, exact_github_url, from_event
from alicedev.dsl.invocation import FromEvent, SessionArg
from alicedev.dsl.model import (
    Action,
    AgentState,
    Audience,
    ChatsAction,
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
    Scenario,
    SendAction,
    SessionArchiveAction,
    SessionRenameAction,
    SessionShowAction,
    SessionSwitchAction,
    ShareAction,
    StartAction,
    StatusAction,
    Tmpl,
)
from alicedev.dsl.render import BASE_VARS, MESSAGE_VARS, RESULT_VARS, ROUTE_VARS
from alicedev.domain import FavoriteView
from alicedev.github.client import GithubClient, GithubFetchError
from alicedev.images import ImageDownloadError, download_quoted_images
from alicedev.render.pagination import clamp_page, page_count
from alicedev.scheduler.engine import FromAgent, FromChat, StartOutcome, StartRequest

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from alicedev.config import PluginConfig
    from alicedev.outbox.service import Outbox
    from alicedev.scheduler.engine import Scheduler
    from alicedev.status import BotStatus
    from alicedev.store.agents_repo import AgentRow, AgentsRepo
    from alicedev.store.db import Store
    from alicedev.store.exchanges_repo import Exchange, ExchangesRepo
    from alicedev.store.favorites_repo import FavoritesRepo
    from alicedev.store.sessions_repo import SessionRow, SessionsRepo

_LOG = logging.getLogger("alicedev.actions")

_SILENT = "__silent__"
_DENIED = "__denied__"  # owner check failed: silent in chat, 403 for agents

_RECENT_LIMIT = 10
_RECENT_TEXT_CHARS = 500


@dataclass
class Outcome:
    result: str
    vars: dict[str, Any] = field(default_factory=dict)
    card: tuple[str, dict[str, Any]] | None = None
    session_id: int | None = None
    replay: bool = False  # an agent retry of a call that already took effect: don't post again


@dataclass(frozen=True)
class AgentCall:
    """``POST /v1/commands`` body (§6.1)."""

    agent_ref: str
    call_id: str
    text: str
    chat_key: str | None
    quote: str | None


@dataclass(frozen=True)
class AgentCallResult:
    status: int  # HTTP status
    body: Mapping[str, Any]


@dataclass(frozen=True)
class AgentCaller:
    """The agent behind an ``alicedev run`` call, validated as current (§6.1)."""

    agent: "AgentRow"
    session: "SessionRow"
    scenario: Scenario
    state: AgentState
    call_id: str
    quote: "Exchange | None" = None


class _AgentReject(Exception):
    def __init__(self, status: int, error: str, message: str | None = None) -> None:
        super().__init__(error)
        self.result = AgentCallResult(
            status, {"error": error, **({"message": message} if message else {})}
        )


@dataclass
class Invocation:
    """What an action needs about the message that triggered it."""

    inbound: Inbound
    args: Mapping[str, Any]
    command: Command | None  # None for routes
    path: tuple[str, ...] = ()  # command words as typed, e.g. ("会话列表", "全部")
    # Session args resolved by the action, exposed to ``say`` as ``session``-shaped dicts.
    resolved: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    # Set for ``alicedev run`` calls; ``inbound`` then describes the principal in the target chat.
    agent: AgentCaller | None = None


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
        agents: "AgentsRepo",
        exchanges: "ExchangesRepo",
        status_fn: Callable[[], Awaitable["BotStatus"]],
    ) -> None:
        self._config = config
        self._store = store
        self._sessions = sessions
        self._favorites = favorites
        self._scheduler = scheduler
        self._outbox = outbox
        self._github = github
        self._registry = registry
        self._agents = agents
        self._exchanges = exchanges
        self._status_fn = status_fn

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
        if outcome.result in (_SILENT, _DENIED):
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
        if inv.agent is not None and no is None:
            raise _AgentReject(400, "usage_error", "agent 调用必须写明 %n")
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
                target = self._scheduler.scenario(row.scenario)
                if target is not None and target.audience is Audience.ADMIN and not inbound.is_admin:
                    _LOG.info("admin-audience session %s: input from %s ignored", row.session_id, inbound.user_key)
                    return Outcome(_DENIED)
                result = await self._scheduler.send(
                    row, text=self._render(inv, text),
                    platform_message_id=inbound.platform_message_id, sender_key=inbound.user_key,
                    sender_name=inbound.sender_name, content=inbound.text,
                )
                if result == "duplicate":
                    return Outcome(_SILENT)
                return Outcome(result, {"session": _session_ctx(row)}, session_id=row.session_id)
            case SessionShowAction(session=arg):
                row = await self._session_arg(inv, arg)
                if row is None:
                    return Outcome("not_found")
                view = await self._sessions.view(row)
                name, fields = dsl_api.session_card(view, self._scheduler.scenario(row.scenario))
                if inv.agent is not None:
                    fields = {**fields, "recent": await self._recent(row)}
                return Outcome("ok", card=(name, fields), session_id=row.session_id)
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
                    return Outcome(_DENIED)
                await self._sessions.rename(row.session_id, self._render(inv, name))
                renamed = await self._sessions.get(row.session_id)
                return Outcome("ok", {"session": _session_ctx(renamed)}, session_id=row.session_id)
            case SessionArchiveAction(session=arg):
                row = await self._session_arg(inv, arg)
                if row is None:
                    return Outcome("not_found")
                if not self._owner_ok(inbound, row, permission):
                    return Outcome(_DENIED)
                if inv.agent is not None and row.session_id == inv.agent.session.session_id:
                    raise _AgentReject(403, "self_archive")
                await self._scheduler.archive(row)
                return Outcome("ok", {"session": _session_ctx(row)}, session_id=row.session_id)
            case HumanAction(session=arg, command=command):
                row = await self._session_arg(inv, arg)
                if row is None:
                    return Outcome("not_found")
                if not self._owner_ok(inbound, row, permission):
                    return Outcome(_DENIED)
                result = await self._scheduler.human(row, command)
                after = await self._sessions.get(row.session_id)
                return Outcome(result, {"session": _session_ctx(after)}, session_id=row.session_id)
            case ShareAction(session=arg, to=to):
                return await self._share(inv, arg, to)
            case FavoriteAction():
                return await self._favorite(inv)
            case ListAction():
                return await self._list(inv, action)
            case ChatsAction():
                chats = await self._sessions.known_chats(self._scheduler.ended_states())
                return Outcome("ok", card=dsl_api.chats_card(chats, self._config.allowed_chats))
            case StatusAction():
                return Outcome("ok", card=dsl_api.status_card(await self._status_fn()))
            case HelpAction(command=arg):
                return await self._help(inv, arg)

    async def _recent(self, row: "SessionRow") -> list[dict[str, Any]]:
        """Latest exchanges of a session, for agents (§6.1 ``recent``)."""
        return [
            {
                "id": item.id,
                "kind": item.kind,
                "author": item.author_name,
                "text": item.text[:_RECENT_TEXT_CHARS],
                "images": len(item.images),
                "at": item.at.strftime("%Y-%m-%d %H:%M"),
            }
            for item in await self._exchanges.recent(row.session_id, _RECENT_LIMIT)
        ]

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
        if inv.agent is not None and inv.agent.session.assigned_by is not None:
            raise _AgentReject(403, "nested_assignment")
        prompt_input = {**inbound.base_vars(), "text": text, "github": github}
        origin: FromChat | FromAgent = (
            FromAgent(call_id=inv.agent.call_id, assigned_by=inv.agent.session.session_id)
            if inv.agent is not None
            else FromChat(
                platform_message_id=inbound.platform_message_id,
                sender_name=inbound.sender_name, content=inbound.text,
            )
        )
        result = await self._scheduler.start(
            StartRequest(
                chat_key=inbound.chat_key, user_key=inbound.user_key, scenario=scenario,
                input=prompt_input, origin=origin,
            )
        )
        if result.outcome is StartOutcome.DUPLICATE and inv.agent is not None and result.session is not None:
            return Outcome("created", {"session": _session_ctx(result.session), "reason": None},
                           session_id=result.session.session_id, replay=True)
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
        if inv.agent is not None:
            return await self._agent_favorite(inv, inv.agent)
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

    async def _agent_favorite(self, inv: Invocation, caller: AgentCaller) -> Outcome:
        item = caller.quote
        if item is None:
            raise _AgentReject(400, "usage_error", "收藏需要 --quote <消息 id>（取自 /会话 %n 的 recent）")
        chat = inv.inbound.chat_key
        dedupe = f"call:{caller.call_id}"
        existing = await self._favorites.by_platform_message_id(chat, dedupe)
        if existing is not None:
            return Outcome("ok", {"id": existing.id}, replay=True)
        try:
            images = await download_quoted_images(item.images, self._config.images_root)
        except ImageDownloadError:
            return Outcome("image_failed")
        record_id = await self._favorites.insert(
            chat_key=chat, saver_key=inv.inbound.user_key, saver_name=inv.inbound.sender_name,
            author_key=item.author_key or f"alicedev:s{item.session_id}", author_name=item.author_name,
            text=item.text, images=[str(p) for p in images], platform_message_id=dedupe,
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

    # --- agent calls (§6.1) ---------------------------------------------------------------

    async def _caller(self, agent_ref: str, call_id: str) -> AgentCaller:
        agent = await self._agents.get(agent_ref)
        if agent is None:
            raise _AgentReject(404, "agent_unknown")
        row = await self._sessions.get(agent.session_id)
        scenario = self._scheduler.scenario(row.scenario) if row is not None else None
        if row is None or scenario is None or row.state != agent.state:
            raise _AgentReject(409, "agent_not_current")
        try:
            state = scenario.state(row.state)
        except KeyError:
            raise _AgentReject(409, "agent_not_current") from None
        latest = await self._agents.for_state(row.session_id, row.state)
        if not isinstance(state, AgentState) or latest is None or latest.agent_ref != agent.agent_ref:
            raise _AgentReject(409, "agent_not_current")
        return AgentCaller(agent=agent, session=row, scenario=scenario, state=state, call_id=call_id)

    async def agent_commands(self, agent_ref: str) -> AgentCallResult:
        """``GET /v1/commands``: the commands this agent's state allows."""
        try:
            caller = await self._caller(agent_ref, call_id="")
        except _AgentReject as reject:
            return reject.result
        registry = self._registry()
        commands = []
        for path in caller.state.agent_commands:
            command = registry.command_at(path)
            if command is None:
                continue
            commands.append({
                "path": path, "usage": command.usage, "summary": command.summary,
                "examples": list(command.examples), "permission": command.permission.value,
            })
        return AgentCallResult(200, {
            "agent": agent_ref, "session_no": caller.session.no, "chat_key": caller.session.chat_key,
            "audience": caller.scenario.audience.value, "commands": commands,
        })

    async def run_agent(self, call: AgentCall) -> AgentCallResult:
        """``POST /v1/commands``: run one command line for an agent (§6.1)."""
        try:
            return await self._run_agent(call)
        except _AgentReject as reject:
            return reject.result

    async def _run_agent(self, call: AgentCall) -> AgentCallResult:
        caller = await self._caller(call.agent_ref, call.call_id)
        registry = self._registry()
        matched = dsl_api.match(registry, call.text)
        if matched is None:
            raise _AgentReject(400, "unknown_command")
        head = registry.commands[matched.path[0]].name  # an alias resolves to the canonical name
        path = " ".join((head, *matched.path[1:]))
        if path not in caller.state.agent_commands:
            raise _AgentReject(403, "command_not_allowed", f"本状态不能调用 {path}")
        command = matched.command
        parsed = dsl_api.parse_args(matched)
        if isinstance(parsed, dsl_api.ArgsError):
            raise _AgentReject(400, "usage_error", f"{parsed.message}；用法：{command.usage}")

        chat_key = caller.session.chat_key
        if call.chat_key is not None and call.chat_key != chat_key:
            if caller.scenario.audience is not Audience.ADMIN or not self._config.chat_allowed(call.chat_key):
                raise _AgentReject(403, "chat_not_allowed")
            chat_key = call.chat_key

        principal = caller.session.created_by
        is_admin = self._config.is_admin(principal)
        if command.permission is Permission.ADMIN and not is_admin:
            raise _AgentReject(403, "permission_denied")

        if isinstance(command.do, FavoriteAction):
            if call.quote is None:
                raise _AgentReject(400, "usage_error", "这条指令需要 --quote <消息 id>")
            item = await self._exchanges.get(call.quote)
            if item is None or item.chat_key != chat_key:
                raise _AgentReject(404, "quote_not_found")
            caller = AgentCaller(
                agent=caller.agent, session=caller.session, scenario=caller.scenario,
                state=caller.state, call_id=caller.call_id, quote=item,
            )

        platform_id, _, sender_id = principal.partition(":")
        sender = caller.session.input.get("sender") or {}
        inbound = Inbound(
            chat_key=chat_key, platform_id=platform_id, user_key=principal, sender_id=sender_id,
            sender_name=str(sender.get("name") or principal), chat_name=await self._chat_name(chat_key),
            text=call.text, platform_message_id=None, is_admin=is_admin, addressed=False,
        )
        inv = Invocation(inbound=inbound, args=parsed.values, command=command, path=matched.path,
                         agent=caller)
        outcome = await self.execute(inv, command.do, command.permission)
        return await self._agent_response(inv, command, outcome)

    async def _chat_name(self, chat_key: str) -> str:
        for chat in await self._sessions.known_chats(self._scheduler.ended_states()):
            if chat.chat_key == chat_key:
                return chat.name
        return ""

    async def _agent_response(self, inv: Invocation, command: Command, outcome: Outcome) -> AgentCallResult:
        if outcome.result == _DENIED:
            raise _AgentReject(403, "permission_denied")
        if outcome.result == _SILENT:
            return AgentCallResult(200, {"result": "unchanged"})
        if outcome.card is not None:
            _, fields = outcome.card
            return AgentCallResult(200, {"result": outcome.result, "data": _jsonable(fields)})
        body: dict[str, Any] = {"result": outcome.result}
        data = {k: v for k, v in outcome.vars.items() if k in ("session", "id") and v is not None}
        if data:
            body["data"] = _jsonable(data)
        tmpl = command.say.get(outcome.result) or self._registry().messages.get(
            dsl_api.message_key(command.do, outcome.result)
        )
        if tmpl is not None:
            text = dsl_api.render(tmpl, self._ctx(inv, outcome.vars))
            if text.strip():
                body["message"] = text
                if not outcome.replay:
                    await self._outbox.enqueue_text(inv.inbound.chat_key, text, session_id=outcome.session_id)
        return AgentCallResult(200, body)


def _jsonable(value: Any) -> Any:
    """Card fields may hold datetimes and tuples; the API speaks JSON."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
