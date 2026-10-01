"""Typed capabilities for agents. No chat command parser or card renderer."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, assert_never

import duckdb

from alicedev.agent_tools.model import (
    ADMIN_TOOLS, TOOL_SPECS, ChatsList, FavoriteAdd, FavoritesList, GithubAnalysisStart,
    InvestigationStart, RequirementAdd, RequirementsList, SessionArchive, SessionGet,
    SessionLinkDeliver, SessionList, SessionRename, StatusGet, ToolError, ToolFailure,
    ToolRequest,
)
from alicedev.agent_tools.results import (
    Chats, LinkReceipt, RecordReceipt, SessionDetails, StartReceipt, ToolDiscovery,
    ToolResult, ToolSuccess,
)
from alicedev.config import PluginConfig
from alicedev.dsl.model import AgentState, Audience, Scenario
from alicedev.gateway_client import GatewayClient, GatewayError
from alicedev.github.client import GithubClient, GithubFetchError
from alicedev.github.models import GithubItem, GithubKind
from alicedev.images import ImageDownloadError, download_quoted_images
from alicedev.paseo.control import PaseoControl
from alicedev.render.pagination import Page, clamp_page, page_count
from alicedev.scheduler.engine import FromAgent, Scheduler, StartRequest
from alicedev.status import BotStatus
from alicedev.store.agents_repo import AgentsRepo
from alicedev.store.db import Store
from alicedev.store.exchanges_repo import Exchange, ExchangesRepo
from alicedev.store.favorites_repo import FavoritesRepo
from alicedev.store.outbox_repo import OutboxRepo
from alicedev.store.requirements_repo import (
    NewRequirement, RequirementQuote, RequirementSource, RequirementSourceKind, RequirementsRepo,
)
from alicedev.store.sessions_repo import SessionRow, SessionsRepo

_LOG = logging.getLogger("alicedev.agent_tools")


@dataclass(frozen=True)
class Caller:
    agent_ref: str
    session: SessionRow
    scenario: Scenario
    state: AgentState
    principal: str
    name: str
    is_admin: bool


class _Reject(Exception):
    def __init__(self, error: ToolError, status: int) -> None:
        super().__init__(error.value)
        self.failure = ToolFailure(error, status)


class ToolService:
    def __init__(self, *, store: Store, config: PluginConfig, sessions: SessionsRepo,
                 agents: AgentsRepo, requirements: RequirementsRepo, favorites: FavoritesRepo,
                 exchanges: ExchangesRepo, scheduler: Scheduler, paseo: PaseoControl,
                 gateway: GatewayClient, github: GithubClient,
                 status_fn: Callable[[], Awaitable[BotStatus]]) -> None:
        self._store, self._config = store, config
        self._sessions, self._agents = sessions, agents
        self._requirements, self._favorites, self._exchanges = requirements, favorites, exchanges
        self._scheduler, self._paseo, self._gateway = scheduler, paseo, gateway
        self._github, self._status_fn = github, status_fn
        self._outbox = OutboxRepo(store)

    async def _caller(self, ref: str) -> Caller:
        agent = await self._agents.get(ref)
        if agent is None:
            raise _Reject(ToolError.AGENT_UNKNOWN, 404)
        row = await self._sessions.get(agent.session_id)
        scenario = self._scheduler.scenario(row.scenario) if row is not None else None
        if row is None or scenario is None or row.state != agent.state:
            raise _Reject(ToolError.AGENT_NOT_CURRENT, 409)
        try:
            state = scenario.state(row.state)
        except KeyError:
            raise _Reject(ToolError.AGENT_NOT_CURRENT, 409) from None
        latest = await self._agents.for_state(row.session_id, row.state)
        if not isinstance(state, AgentState) or latest is None or latest.agent_ref != agent.agent_ref:
            raise _Reject(ToolError.AGENT_NOT_CURRENT, 409)
        sender = row.input.get("sender")
        name = sender.get("name") if isinstance(sender, dict) else None
        return Caller(agent.agent_ref, row, scenario, state, row.created_by,
                      name if isinstance(name, str) and name else row.created_by,
                      self._config.is_admin(row.created_by))

    async def discover(self, ref: str) -> ToolDiscovery | ToolFailure:
        try:
            caller = await self._caller(ref)
            return ToolDiscovery(caller.agent_ref, caller.session.no, caller.session.chat_key,
                                 tuple(spec for spec in TOOL_SPECS if spec.name in caller.state.tools))
        except _Reject as exc:
            return exc.failure

    def _chat(self, caller: Caller, requested: str | None) -> str:
        chat = requested if requested is not None else caller.session.chat_key
        if not self._config.chat_allowed(chat):
            raise _Reject(ToolError.CHAT_NOT_ALLOWED, 403)
        if chat != caller.session.chat_key and (caller.scenario.audience is not Audience.ADMIN or not caller.is_admin):
            raise _Reject(ToolError.CHAT_NOT_ALLOWED, 403)
        return chat

    async def _session(self, chat: str, no: int) -> SessionRow:
        row = await self._sessions.by_no(chat, no)
        if row is None:
            raise _Reject(ToolError.SESSION_NOT_FOUND, 404)
        return row

    async def _quote(self, chat: str, ref: str) -> Exchange:
        item = await self._exchanges.get(ref)
        if item is None or item.chat_key != chat:
            raise _Reject(ToolError.QUOTE_NOT_FOUND, 404)
        return item

    async def invoke(self, request: ToolRequest) -> ToolSuccess | ToolFailure:
        try:
            caller = await self._caller(request.agent_ref)
            invocation = request.invocation
            if invocation.name not in caller.state.tools:
                raise _Reject(ToolError.TOOL_NOT_ALLOWED, 403)
            if invocation.name in ADMIN_TOOLS and not caller.is_admin:
                raise _Reject(ToolError.PERMISSION_DENIED, 403)
            match invocation:
                case ChatsList():
                    chats = await self._sessions.known_chats(self._scheduler.ended_states())
                    return ToolSuccess(ToolResult.OK, Chats(tuple(chats), self._config.allowed_chats))
                case StatusGet():
                    return ToolSuccess(ToolResult.OK, await self._status_fn())
                case _:
                    chat = self._chat(caller, invocation.chat)
            match invocation:
                case SessionGet(no=no):
                    row = await self._session(chat, no)
                    recent = tuple(replace(item, text=item.text[:500]) for item in await self._exchanges.recent(row.session_id, 10))
                    return ToolSuccess(ToolResult.OK, SessionDetails(await self._sessions.view(row), recent))
                case SessionList(page=page, include_ended=include):
                    rows, total = await self._sessions.page(chat, page=1, scenario=None, include_ended=include,
                                                            ended_states=self._scheduler.ended_states())
                    pages = page_count(total)
                    current = clamp_page(page, pages)
                    if current != 1:
                        rows, total = await self._sessions.page(chat, page=current, scenario=None, include_ended=include,
                                                                ended_states=self._scheduler.ended_states())
                    return ToolSuccess(ToolResult.OK, Page([await self._sessions.view(row) for row in rows], current, pages, total, 10))
                case RequirementsList(page=page):
                    return ToolSuccess(ToolResult.OK, await self._requirements.list_page(chat, page))
                case FavoritesList(page=page):
                    return ToolSuccess(ToolResult.OK, await self._favorites.list_page(chat, page))
                case RequirementAdd(text=text, quote=quote_ref):
                    source = RequirementSource(RequirementSourceKind.AGENT_CALL, f"{caller.agent_ref}:{request.call_id}")
                    existing = await self._requirements.by_source(chat, source)
                    if existing is not None:
                        return ToolSuccess(ToolResult.OK, RecordReceipt(existing.id))
                    quote = None
                    if quote_ref is not None:
                        item = await self._quote(chat, quote_ref)
                        paths = await download_quoted_images(item.images, self._config.images_root)
                        quote = RequirementQuote(item.author_key, item.author_name, item.text, tuple(str(path) for path in paths))
                    saved = await self._requirements.save(NewRequirement(chat, caller.principal, caller.name, text, (), quote, source))
                    return ToolSuccess(ToolResult.OK, RecordReceipt(saved.record.id))
                case FavoriteAdd(quote=ref):
                    item = await self._quote(chat, ref)
                    identity = f"agent:{caller.agent_ref}:{request.call_id}"
                    existing = await self._favorites.by_platform_message_id(chat, identity)
                    if existing is not None:
                        return ToolSuccess(ToolResult.OK, RecordReceipt(existing.id))
                    images = await download_quoted_images(item.images, self._config.images_root)
                    record_id = await self._save_favorite(caller, item, identity, tuple(str(path) for path in images))
                    return ToolSuccess(ToolResult.OK, RecordReceipt(record_id))
                case InvestigationStart(text=text):
                    return await self._start(caller, request.call_id, chat, "investigate", text, None)
                case GithubAnalysisStart(url=url):
                    ref = self._github.try_parse(url)
                    if ref is None:
                        raise _Reject(ToolError.FETCH_FAILED, 400)
                    item = await self._github.fetch(ref)
                    match item.kind:
                        case GithubKind.ISSUE:
                            scenario = "github-issue"
                        case GithubKind.PR:
                            scenario = "github-pr"
                        case _ as unreachable_kind:
                            assert_never(unreachable_kind)
                    return await self._start(caller, request.call_id, chat, scenario, url, item)
                case SessionArchive(no=no):
                    row = await self._session(chat, no)
                    if row.session_id == caller.session.session_id:
                        raise _Reject(ToolError.SELF_ARCHIVE, 403)
                    self._owner(caller, row)
                    if not self._scheduler.is_ended(row):
                        await self._scheduler.archive(row)
                    after = await self._sessions.get(row.session_id)
                    assert after is not None
                    return ToolSuccess(ToolResult.OK, await self._sessions.view(after))
                case SessionRename(no=no, name_text=name):
                    row = await self._session(chat, no)
                    self._owner(caller, row)
                    await self._sessions.rename(row.session_id, name)
                    after = await self._sessions.get(row.session_id)
                    assert after is not None
                    return ToolSuccess(ToolResult.OK, await self._sessions.view(after))
                case SessionLinkDeliver(no=no):
                    row = await self._session(chat, no)
                    return await self._link(caller, request.call_id, row)
                case ChatsList() | StatusGet():
                    raise AssertionError("handled before chat resolution")
                case _ as unreachable:
                    assert_never(unreachable)
        except _Reject as exc:
            return exc.failure
        except ImageDownloadError:
            return ToolFailure(ToolError.IMAGE_FAILED, 502)
        except GithubFetchError:
            return ToolFailure(ToolError.FETCH_FAILED, 502)
        except GatewayError:
            _LOG.exception("tool link signing failed")
            return ToolFailure(ToolError.LINK_FAILED, 502)

    @staticmethod
    def _owner(caller: Caller, row: SessionRow) -> None:
        if not caller.is_admin and caller.principal != row.created_by:
            raise _Reject(ToolError.PERMISSION_DENIED, 403)

    async def _start(self, caller: Caller, call_id: str, chat: str, scenario: str,
                     text: str, github: GithubItem | None) -> ToolSuccess:
        if caller.session.assigned_by is not None:
            raise _Reject(ToolError.NESTED_ASSIGNMENT, 403)
        chat_name = next((item.name for item in await self._sessions.known_chats(self._scheduler.ended_states()) if item.chat_key == chat), "")
        result = await self._scheduler.start(StartRequest(
            chat_key=chat, user_key=caller.principal, scenario=scenario,
            input={"text": text, "github": GithubClient.to_prompt_var(github) if github is not None else None,
                   "sender": {"id": caller.principal, "name": caller.name},
                   "chat": {"key": chat, "name": chat_name}, "images": [], "quoted": None},
            origin=FromAgent(agent_ref=caller.agent_ref, call_id=call_id, assigned_by=caller.session.session_id),
        ))
        view = await self._sessions.view(result.session) if result.session is not None else None
        return ToolSuccess(ToolResult.OK, StartReceipt(result.outcome, view))

    async def _save_favorite(self, caller: Caller, item: Exchange, identity: str, images: tuple[str, ...]) -> int:
        async with self._store.lock:
            existing = await self._favorites.by_platform_message_id(item.chat_key, identity)
            if existing is not None:
                return existing.id
            record_id = await self._store.next_id("seq_favorites")
            await self._store.execute(
                "INSERT INTO favorites (id,chat_key,saver_key,saver_name,author_key,author_name,text,images,platform_message_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (record_id, item.chat_key, caller.principal, caller.name,
                 item.author_key or f"alicedev:s{item.session_id}", item.author_name, item.text,
                 json.dumps(images, ensure_ascii=False), identity),
            )
            return record_id

    async def _link(self, caller: Caller, call_id: str, row: SessionRow) -> ToolSuccess:
        digest = hashlib.sha256(json.dumps(["session_link_deliver", caller.agent_ref, call_id], separators=(",", ":")).encode()).hexdigest()
        delivery_id = f"tool_{digest}"
        replay = await self._link_replay(delivery_id, caller, row)
        if replay is not None:
            return replay
        if self._scheduler.is_ended(row):
            return ToolSuccess(ToolResult.NOT_READY, None)
        agent = await self._agents.latest(row.session_id)
        target = self._scheduler.share_target(row, agent, await self._paseo.server_id())
        if target is None:
            return ToolSuccess(ToolResult.NOT_READY, None)
        issued = await self._gateway.issue_token(target=target, user_key=caller.principal,
                                                 session_id=row.session_id, ttl_s=self._config.share_ttl_seconds)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        payload = {"type": "at_text", "platform_id": caller.principal.partition(":")[2],
                   "name": caller.name, "text": f" {issued.url}"}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        async with self._store.lock:
            replay = await self._link_replay(delivery_id, caller, row)
            if replay is not None:
                return replay
            fresh = await self._sessions.get(row.session_id)
            if fresh is None or self._scheduler.is_ended(fresh):
                return ToolSuccess(ToolResult.NOT_READY, None)
            def commit(conn: duckdb.DuckDBPyConnection) -> None:
                conn.execute("INSERT INTO tokens_issued (token_id,session_id,user_key,issued_by,target,issued_at,expires_at) VALUES (?,?,?,?,?,?,?)",
                             (delivery_id, row.session_id, caller.principal, caller.principal, target, now,
                              now + timedelta(seconds=self._config.share_ttl_seconds)))
                conn.execute("INSERT INTO outbox (reply_id,seq,chat_key,session_id,agent_ref,msgs,payload,payload_sha256,state,next_attempt_at) "
                             "VALUES (?,nextval('seq_outbox'),?,?,NULL,'[]',?,?,'queued',CURRENT_TIMESTAMP AT TIME ZONE 'UTC')",
                             (delivery_id, caller.session.chat_key, caller.session.session_id, encoded, hashlib.sha256(encoded.encode()).hexdigest()))
            await self._store.transaction(commit)
        return ToolSuccess(ToolResult.QUEUED, LinkReceipt(delivery_id, row.no))

    async def _link_replay(self, delivery_id: str, caller: Caller, row: SessionRow) -> ToolSuccess | None:
        previous = await self._outbox.get(delivery_id)
        if previous is None:
            return None
        audit = await self._store.fetch_one("SELECT session_id,user_key FROM tokens_issued WHERE token_id = ?", (delivery_id,))
        if previous.chat_key != caller.session.chat_key or previous.session_id != caller.session.session_id or audit != (row.session_id, caller.principal):
            raise _Reject(ToolError.CALL_ID_CONFLICT, 409)
        return ToolSuccess(ToolResult.QUEUED, LinkReceipt(delivery_id, row.no))
