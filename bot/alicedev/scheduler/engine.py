"""Session state machine (ARCHITECTURE §7).

The scheduler is the only component that changes session state outside the
§6 reply intake, and the only one that performs a state's side effects (start
an agent, archive agents, finish a session, issue share links, dispatch the
next queued session of an exclusive scenario).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Mapping
from urllib.parse import quote

from alicedev import dsl_api
from alicedev.dsl.messages import STATE_DEFAULT_KEY, state_key
from alicedev.dsl.render import MESSAGE_VARS, PROMPT_VARS, TITLE_VARS, context
from alicedev.dsl.model import (
    ARCHIVED,
    BUILTIN_STATES,
    FAILED,
    MAIN_SYNC_FAILED,
    QUEUED,
    AgentState,
    CwdWorkdir,
    HumanState,
    Registry,
    RepoWorkdir,
    Scenario,
    TerminalState,
    is_visible,
)
from alicedev.ids import new_agent_ref
from alicedev.paseo import mainsync
from alicedev.paseo.agent_actor import (
    AgentStartDeferred,
    AgentStartFailed,
    AgentStarted,
    InjectOutcome,
)
from alicedev.paseo.control import PaseoError
from alicedev.store.agents_repo import AgentRow, AgentRowStatus

if TYPE_CHECKING:
    from alicedev.config import PluginConfig
    from alicedev.gateway_client import GatewayClient
    from alicedev.outbox.service import Outbox
    from alicedev.paseo.agent_actor import AgentActor
    from alicedev.paseo.control import PaseoControl
    from alicedev.store.agents_repo import AgentsRepo
    from alicedev.store.db import Store
    from alicedev.store.messages_repo import MessagesRepo
    from alicedev.store.sessions_repo import SessionRow, SessionsRepo

_LOG = logging.getLogger("alicedev.scheduler")

SLOTS_FULL = "在线会话已满，有空位后自动开始"
TRANSITION_SLOT_WAIT_SECONDS = 60.0  # a state change of a running session may wait this long for a slot
WAITER_INTERVAL_SECONDS = 10.0


class StartOutcome(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    WAITING = "waiting"  # no online slot free: stays queued until one is (§4 在线名额)
    FAILED = "failed"
    SENT = "sent"  # one_per_chat: the chat's open session got the text instead
    BUSY = "busy"  # one_per_chat: that session could not take it now
    DUPLICATE = "duplicate"  # platform redelivery / CLI retry: already handled, say nothing
    NOTIFIED = "notified"  # ended in main_sync_failed: its state notice (with link) is the reply


@dataclass(frozen=True)
class FromChat:
    """A person in the chat triggered the start (command or route)."""

    platform_message_id: str | None
    sender_name: str
    content: str  # the platform message text as sent


@dataclass(frozen=True)
class FromAgent:
    """An agent's ``alicedev run`` triggered the start (§6.1)."""

    call_id: str
    assigned_by: int  # the calling agent's session

    @property
    def dedupe_key(self) -> str:
        return f"call:{self.call_id}"


Origin = FromChat | FromAgent


@dataclass(frozen=True)
class StartRequest:
    chat_key: str
    user_key: str
    scenario: str
    input: Mapping[str, Any]  # prompt variables captured at trigger time
    origin: Origin


@dataclass(frozen=True)
class StartResult:
    outcome: StartOutcome
    session: "SessionRow | None"
    reason: str = ""


class EnteredBy(str, Enum):
    AI = "ai"
    HUMAN = "human"
    SYSTEM = "system"


class Scheduler:
    def __init__(
        self,
        *,
        store: "Store",
        sessions: "SessionsRepo",
        agents: "AgentsRepo",
        messages: "MessagesRepo",
        actor: "AgentActor",
        paseo: "PaseoControl",
        outbox: "Outbox",
        gateway: "GatewayClient | None",
        config: "PluginConfig",
        registry: Callable[[], Registry],
    ) -> None:
        self._store = store
        self._sessions = sessions
        self._agents = agents
        self._messages = messages
        self._actor = actor
        self._paseo = paseo
        self._outbox = outbox
        self._gateway = gateway
        self._config = config
        self._registry = registry
        self._scenario_locks: dict[str, asyncio.Lock] = {}
        self._background: set[asyncio.Task] = set()
        self._retry_lock = asyncio.Lock()

    # --- registry helpers -------------------------------------------------------

    def scenario(self, name: str) -> Scenario | None:
        return self._registry().scenarios.get(name)

    def ended_states(self) -> tuple[str, ...]:
        names = {MAIN_SYNC_FAILED, FAILED, ARCHIVED}
        for scenario in self._registry().scenarios.values():
            names.update(n for n, s in scenario.states.items() if isinstance(s, TerminalState))
        return tuple(sorted(names))

    def is_ended(self, row: "SessionRow") -> bool:
        scenario = self.scenario(row.scenario)
        if scenario is None:
            return row.state in (MAIN_SYNC_FAILED, FAILED, ARCHIVED)
        if row.state == QUEUED:
            return False
        try:
            return isinstance(scenario.state(row.state), TerminalState)
        except KeyError:
            return True

    async def is_conversational_agent(self, agent: AgentRow) -> bool:
        row = await self._sessions.get(agent.session_id)
        if row is None or row.state != agent.state:
            return False
        scenario = self.scenario(row.scenario)
        if scenario is None:
            return False
        try:
            state = scenario.state(agent.state)
        except KeyError:
            return False
        return isinstance(state, AgentState) and state.conversational

    def _lock(self, scenario: str) -> asyncio.Lock:
        return self._scenario_locks.setdefault(scenario, asyncio.Lock())

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def stop(self) -> None:
        for task in list(self._background):
            task.cancel()
        for task in list(self._background):
            try:
                await task
            except BaseException:  # noqa: BLE001 - shutting down
                pass

    # --- new session --------------------------------------------------------------

    async def start(self, req: StartRequest) -> StartResult:
        scenario = self.scenario(req.scenario)
        if scenario is None:
            return StartResult(StartOutcome.FAILED, None, f"未知场景 {req.scenario}")
        from alicedev.ids import new_msg_ref

        match req.origin:
            case FromChat(platform_message_id=pmid, sender_name=sender_name, content=content):
                dedupe_key, assigned_by, claim_name, claim_content = pmid, None, sender_name, content
            case FromAgent(assigned_by=parent) as agent_origin:
                dedupe_key, assigned_by, claim_name, claim_content = agent_origin.dedupe_key, parent, None, None
        text = str(req.input.get("text", ""))
        async with self._store.lock:
            if dedupe_key is not None:
                seen = await self._store.fetch_one(
                    "SELECT session_id FROM messages WHERE chat_key = ? AND platform_message_id = ?",
                    (req.chat_key, dedupe_key),
                )
                if seen is not None:
                    existing = await self._sessions.get(int(seen[0])) if seen[0] is not None else None
                    return StartResult(StartOutcome.DUPLICATE, existing)
            open_one = await self._open_in_chat(scenario, req.chat_key) if scenario.one_per_chat else None
            if open_one is None:
                try:
                    title = dsl_api.render(scenario.title, context(TITLE_VARS, dict(req.input)))
                except Exception:  # noqa: BLE001 - a bad title must not block the session
                    _LOG.exception("title render failed for scenario %s", scenario.name)
                    title = scenario.name
                row = await self._sessions.create(
                    chat_key=req.chat_key, scenario=scenario.name, name=title,
                    created_by=req.user_key, input=req.input, state=QUEUED, assigned_by=assigned_by,
                )
                trigger_ref = new_msg_ref()
                await self._messages.claim(
                    msg_ref=trigger_ref, chat_key=req.chat_key, platform_message_id=dedupe_key,
                    sender_key=req.user_key, sender_name=claim_name, content=claim_content,
                    text=text, session_id=row.session_id, agent_ref=None,
                )
                if isinstance(req.origin, FromChat):
                    await self._sessions.set_current(req.chat_key, row.session_id)

        if open_one is not None:
            return await self._start_into_open(open_one, req, text)
        async with self._lock(scenario.name):
            if scenario.exclusive and await self._exclusive_busy(scenario, row.session_id):
                return StartResult(StartOutcome.QUEUED, row)
            ok, reason = await self._dispatch(row, notify=False, trigger_msg_ref=trigger_ref)
        current = await self._sessions.get(row.session_id)
        if current is not None and current.state == QUEUED:
            return StartResult(StartOutcome.WAITING, current, SLOTS_FULL)
        if ok:
            return StartResult(StartOutcome.CREATED, current)
        if current is not None and current.state == MAIN_SYNC_FAILED:
            return StartResult(StartOutcome.NOTIFIED, current, reason)
        return StartResult(StartOutcome.FAILED, current, reason)

    async def _open_in_chat(self, scenario: Scenario, chat_key: str) -> "SessionRow | None":
        """``one_per_chat``: the chat's unfinished session of this scenario (caller holds the Store lock)."""
        for row in await self._sessions.not_in_states(self.ended_states()):
            if row.chat_key == chat_key and row.scenario == scenario.name:
                return row
        return None

    async def _start_into_open(self, row: "SessionRow", req: StartRequest, text: str) -> StartResult:
        match req.origin:
            case FromAgent():
                return StartResult(StartOutcome.FAILED, row, f"本群已有 %{row.no}，agent 不能向它发消息")
            case FromChat(platform_message_id=pmid, sender_name=sender_name, content=content):
                result = await self.send(
                    row, text=text, platform_message_id=pmid, sender_key=req.user_key,
                    sender_name=sender_name, content=content,
                )
        match result:
            case "ok":
                return StartResult(StartOutcome.SENT, await self._sessions.get(row.session_id))
            case "duplicate":
                return StartResult(StartOutcome.DUPLICATE, row)
            case _:  # busy, or still being dispatched (not yet conversational)
                return StartResult(StartOutcome.BUSY, row)

    async def _exclusive_busy(self, scenario: Scenario, exclude: int) -> bool:
        ended = self.ended_states()
        for other in await self._sessions.not_in_states((*ended, QUEUED)):
            if other.scenario == scenario.name and other.session_id != exclude:
                return True
        # Older queued sessions of the same scenario go first.
        for other in await self._sessions.in_states((QUEUED,)):
            if other.scenario == scenario.name and other.session_id < exclude:
                return True
        return False

    async def _dispatch(
        self, row: "SessionRow", *, notify: bool, trigger_msg_ref: str | None = None
    ) -> tuple[bool, str]:
        scenario = self.scenario(row.scenario)
        if scenario is None:
            await self._enter(row, FAILED, by=EnteredBy.SYSTEM, notify=notify, reason="场景已不存在")
            return False, "场景已不存在"
        if not await self._actor.has_room():
            return False, SLOTS_FULL  # stays queued; retry_waiting picks it up (§4 在线名额)
        match scenario.workdir:
            case CwdWorkdir(path=path) if row.workspace_id is None:
                try:
                    ws = await self._paseo.workspace_local(path, row.name)
                except PaseoError as exc:
                    reason = f"创建工作区失败：{exc}"
                    await self._enter(row, FAILED, by=EnteredBy.SYSTEM, notify=notify, reason=reason)
                    return False, reason
                await self._sessions.set_workspace(row.session_id, workspace_id=ws.workspace_id)
            case RepoWorkdir(fixed_main=fixed_main, base=base) if (
                row.workspace_id is None or row.worktree_path is None
            ):
                result = await mainsync.align(self._paseo, fixed_main)
                match result:
                    case mainsync.SyncFailed(reason=reason):
                        try:
                            ws = await self._paseo.workspace_local(fixed_main, "fixed-main")
                            await self._sessions.set_workspace(row.session_id, workspace_id=ws.workspace_id)
                        except PaseoError:
                            _LOG.warning("fixed-main workspace register failed", exc_info=True)
                        fresh = await self._sessions.get(row.session_id)
                        assert fresh is not None
                        await self._enter(fresh, MAIN_SYNC_FAILED, by=EnteredBy.SYSTEM,
                                          notify=True, reason=reason)
                        return False, reason
                    case mainsync.Aligned(sha=sha):
                        try:
                            ws = await self._paseo.worktree_create(
                                repo=fixed_main, base_ref=base, slug=f"s{row.session_id}"
                            )
                        except PaseoError as exc:
                            reason = f"创建 worktree 失败：{exc}"
                            await self._enter(row, FAILED, by=EnteredBy.SYSTEM, notify=notify,
                                              reason=reason)
                            return False, reason
                        await self._sessions.set_workspace(
                            row.session_id, workspace_id=ws.workspace_id,
                            worktree_path=ws.cwd, base_sha=sha,
                        )
            case _:
                pass  # workspace (and worktree) already exist from an earlier, deferred attempt
        fresh = await self._sessions.get(row.session_id)
        assert fresh is not None
        ok, reason = await self._enter(fresh, scenario.initial, by=EnteredBy.SYSTEM, notify=notify,
                                       trigger_msg_ref=trigger_msg_ref)
        return ok, reason

    # --- entering states ------------------------------------------------------------

    async def _enter(
        self,
        row: "SessionRow",
        target: str,
        *,
        by: EnteredBy,
        notify: bool,
        reason: str = "",
        trigger_msg_ref: str | None = None,
    ) -> tuple[bool, str]:
        prev = row.state
        await self._sessions.set_state(row.session_id, target)
        fresh = await self._sessions.get(row.session_id)
        assert fresh is not None
        return await self.on_entered(fresh, prev, by=by, notify=notify, reason=reason,
                                     trigger_msg_ref=trigger_msg_ref)

    async def after_reply_transition(self, session_id: int, prev_state: str) -> None:
        """Side effects of an AI-reported transition (state already written by §6 intake)."""
        row = await self._sessions.get(session_id)
        if row is None:
            return
        await self.on_entered(row, prev_state, by=EnteredBy.AI, notify=False)

    def schedule_after_reply_transition(self, session_id: int, prev_state: str) -> None:
        self._spawn(self._guard(self.after_reply_transition(session_id, prev_state)))

    async def _guard(self, coro: Any) -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001 - background side effects must not die silently
            _LOG.exception("scheduler background step failed")

    async def on_entered(
        self,
        row: "SessionRow",
        prev: str,
        *,
        by: EnteredBy,
        notify: bool,
        reason: str = "",
        trigger_msg_ref: str | None = None,
    ) -> tuple[bool, str]:
        if prev != row.state:
            for agent in await self._agents.unarchived(row.session_id):
                if agent.state == prev:
                    await self._actor.archive(agent)
        scenario = self.scenario(row.scenario)
        state = scenario.state(row.state) if scenario else TerminalState(name=row.state)
        match state:
            case AgentState():
                assert scenario is not None
                if by is EnteredBy.HUMAN:  # the state text is the human command's reply
                    await self._state_notice(row, reason, share=False)
                wait = 0.0 if prev == QUEUED else TRANSITION_SLOT_WAIT_SECONDS
                started = await self._start_agent(row, scenario, state, trigger_msg_ref, wait_seconds=wait)
                match started:
                    case AgentStarted():
                        return True, ""
                    case AgentStartDeferred() if prev == QUEUED:
                        # A new session waits for a slot; it goes back to the queue (§4).
                        await self._sessions.set_state(row.session_id, QUEUED)
                        return False, SLOTS_FULL
                    case AgentStartDeferred():
                        why = "在线会话已满，等不到名额"
                        await self._enter(row, FAILED, by=EnteredBy.SYSTEM, notify=notify, reason=why)
                        return False, why
                    case AgentStartFailed(reason=why):
                        await self._enter(row, FAILED, by=EnteredBy.SYSTEM, notify=notify, reason=why)
                        return False, why
            case HumanState():
                # An AI-entered state's message is the AI reply (share link
                # appended by the §6 intake); otherwise post the state text.
                if by is not EnteredBy.AI:
                    await self._state_notice(row, reason, share=state.share)
                return True, ""
            case TerminalState():
                silent = (by is EnteredBy.SYSTEM and not notify
                          and row.state in (FAILED, MAIN_SYNC_FAILED) and not state.share)
                if by is not EnteredBy.AI and row.state != ARCHIVED and not silent:
                    await self._state_notice(row, reason, share=state.share)
                await self._finish(row)
                return row.state not in (FAILED, MAIN_SYNC_FAILED), reason
        return True, ""

    async def _start_agent(
        self,
        row: "SessionRow",
        scenario: Scenario,
        state: AgentState,
        trigger_msg_ref: str | None,
        *,
        wait_seconds: float,
    ) -> AgentStarted | AgentStartFailed | AgentStartDeferred:
        if row.workspace_id is None:
            return AgentStartFailed("会话没有工作区")
        match scenario.workdir:
            case CwdWorkdir(path=path):
                cwd = path
            case RepoWorkdir():
                cwd = row.worktree_path or ""
        agent_ref = new_agent_ref()
        registry = self._registry()
        commands = tuple(
            (path, command)
            for path in state.agent_commands
            if (command := registry.command_at(path)) is not None
        )
        try:
            prompt = dsl_api.render(
                state.prompt, context(PROMPT_VARS, self.prompt_vars(row, scenario, state, agent_ref))
            )
            prompt = prompt.rstrip() + "\n\n" + dsl_api.reply_instructions(
                scenario, state, agent_ref=agent_ref, commands=commands
            )
        except Exception as exc:  # noqa: BLE001 - render errors are reported, not raised
            _LOG.exception("prompt render failed for %s/%s", scenario.name, state.name)
            return AgentStartFailed(f"prompt 渲染失败：{exc}")
        outcome = await self._actor.start(
            agent_ref=agent_ref, session_id=row.session_id, chat_key=row.chat_key,
            state=state.name, provider=scenario.provider, cwd=cwd,
            workspace_id=row.workspace_id, title=row.name, prompt=prompt,
            trigger_msg_ref=trigger_msg_ref, sender_key=row.created_by, wait_seconds=wait_seconds,
        )
        match outcome:
            case AgentStartFailed(reason=why):
                return AgentStartFailed(f"启动 agent 失败：{why}")
            case _:
                return outcome

    def prompt_vars(
        self, row: "SessionRow", scenario: Scenario, state: AgentState, agent_ref: str
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "text": "", "sender": None, "chat": None, "quoted": None, "images": [], "github": None,
            "repo": None,
        }
        base.update(dict(row.input))
        base.update(
            {
                "session": self._session_ctx(row),
                "reports_dir": f"{str(self._config.reports_root).rstrip('/')}/s{row.session_id}/",
                "data": dict(row.data),
                "state": state.name,
                "next": list(state.next),
                "agent_ref": agent_ref,
            }
        )
        if isinstance(scenario.workdir, RepoWorkdir):
            base["repo"] = {
                "fixed_main": scenario.workdir.fixed_main,
                "worktree": row.worktree_path,
                "branch": f"alicedev/s{row.session_id}",
                "base_sha": row.base_sha,
            }
        return base

    async def _finish(self, row: "SessionRow") -> None:
        for agent in await self._agents.unarchived(row.session_id):
            await self._actor.archive(agent)
        if row.worktree_path and row.workspace_id:
            try:
                await self._paseo.workspace_archive(row.workspace_id)
            except PaseoError:
                _LOG.warning("worktree archive failed for session %s", row.session_id, exc_info=True)
        await self._sessions.clear_current_if(row.chat_key, row.session_id)
        self._spawn(self._guard(self.retry_waiting()))

    async def _dispatch_next(self, scenario: Scenario) -> None:
        async with self._lock(scenario.name):
            queued = [r for r in await self._sessions.in_states((QUEUED,))
                      if r.scenario == scenario.name]
            if not queued:
                return
            head = queued[0]
            if await self._exclusive_busy(scenario, head.session_id):
                return
            await self._dispatch(head, notify=True)

    async def retry_waiting(self) -> None:
        """Dispatch queued sessions oldest first until one cannot get a slot (§4 在线名额)."""
        async with self._retry_lock:
            for row in await self._sessions.in_states((QUEUED,)):
                scenario = self.scenario(row.scenario)
                if scenario is None:
                    continue
                if scenario.exclusive and await self._exclusive_busy(scenario, row.session_id):
                    continue
                async with self._lock(scenario.name):
                    fresh = await self._sessions.get(row.session_id)
                    if fresh is None or fresh.state != QUEUED:
                        continue
                    await self._dispatch(fresh, notify=True)
                after = await self._sessions.get(row.session_id)
                if after is not None and after.state == QUEUED:
                    return  # no slot: keep first come, first served

    def start_background(self) -> None:
        """Poll for freed slots so queued sessions start without waiting for a trigger."""
        self._spawn(self._guard(self._waiter_loop()))

    async def _waiter_loop(self) -> None:
        while True:
            await asyncio.sleep(WAITER_INTERVAL_SECONDS)
            try:
                await self.retry_waiting()
            except Exception:  # noqa: BLE001 - the waiter must survive one bad pass
                _LOG.exception("slot waiter pass failed")

    # --- human-driven changes ----------------------------------------------------------

    async def human(self, row: "SessionRow", command: str) -> str:
        scenario = self.scenario(row.scenario)
        if scenario is None:
            return "not_allowed"
        async with self._lock(scenario.name):
            fresh = await self._sessions.get(row.session_id)
            if fresh is None:
                return "not_allowed"
            try:
                state = scenario.state(fresh.state)
            except KeyError:
                return "not_allowed"
            if not isinstance(state, HumanState) or command not in state.commands:
                return "not_allowed"
            await self._enter(fresh, state.commands[command], by=EnteredBy.HUMAN, notify=True)
        return "ok"

    async def archive(self, row: "SessionRow") -> str:
        if self.is_ended(row):
            return "ok"
        await self._enter(row, ARCHIVED, by=EnteredBy.HUMAN, notify=False)
        return "ok"

    async def send(
        self,
        row: "SessionRow",
        *,
        text: str,
        platform_message_id: str | None,
        sender_key: str,
        sender_name: str,
        content: str,
    ) -> str:
        scenario = self.scenario(row.scenario)
        if scenario is None:
            return "not_conversational"
        try:
            state = scenario.state(row.state)
        except KeyError:
            return "not_conversational"
        if not (isinstance(state, AgentState) and state.conversational):
            return "not_conversational"
        agent = await self._agents.for_state(row.session_id, row.state)
        if agent is None:
            return "not_conversational"
        outcome = await self._actor.inject(
            agent, chat_key=row.chat_key, text=text,
            platform_message_id=platform_message_id, sender_key=sender_key,
            sender_name=sender_name, content=content,
        )
        match outcome:
            case InjectOutcome.OK:
                await self._sessions.set_current(row.chat_key, row.session_id)
                return "ok"
            case InjectOutcome.DUPLICATE:
                return "duplicate"
            case InjectOutcome.BUSY:
                return "busy"
            case InjectOutcome.FULL:
                return "full"
            case InjectOutcome.FAILED:
                return "not_conversational"

    # --- notices & links --------------------------------------------------------------

    def _session_ctx(self, row: "SessionRow") -> dict[str, Any]:
        return {"no": row.no, "name": row.name, "state": row.state, "label": f"%{row.no}",
                "scenario": row.scenario}

    async def _notice(self, row: "SessionRow", key: str, **extra: Any) -> None:
        tmpl = self._registry().messages.get(key)
        if tmpl is None:
            _LOG.warning("messages.yaml has no %r; notice for session %s skipped", key, row.session_id)
            return
        try:
            text = dsl_api.render(
                tmpl, context(MESSAGE_VARS, {"session": self._session_ctx(row), **extra})
            )
        except Exception:  # noqa: BLE001
            _LOG.exception("notice %s render failed", key)
            return
        await self._outbox.enqueue_text(row.chat_key, text, session_id=row.session_id)

    async def _state_notice(self, row: "SessionRow", reason: str, *, share: bool) -> None:
        link = await self._creator_link(row) if share else None
        key = state_key(row.state)
        if key not in self._registry().messages:
            key = STATE_DEFAULT_KEY
        text_ctx: dict[str, Any] = {"reason": reason or None, "data": dict(row.data), "link": link}
        await self._notice(row, key, **text_ctx)
        if link and "link" not in self._registry().messages[key].source:
            await self._outbox.enqueue_text(row.chat_key, link, session_id=row.session_id)

    def share_target(self, row: "SessionRow", agent: AgentRow | None, server_id: str | None) -> str | None:
        if not server_id:
            return None
        server = quote(server_id, safe="")
        if agent is not None and agent.agent_id and agent.workspace_id:
            return (f"/h/{server}/workspace/{quote(agent.workspace_id, safe='')}"
                    f"?open=agent%3A{quote(agent.agent_id, safe='')}")
        if row.workspace_id:
            return f"/h/{server}/workspace/{quote(row.workspace_id, safe='')}"
        return None

    async def issue_link(self, row: "SessionRow", *, user_key: str, issued_by: str) -> str | None:
        """One-time share URL for ``user_key`` (§8 分享目标), recorded in tokens_issued."""
        if self._gateway is None:
            return None
        agent = await self._agents.latest(row.session_id)
        target = self.share_target(row, agent, await self._paseo.server_id())
        if target is None:
            return None
        issued = await self._gateway.issue_token(
            target=target, user_key=user_key, session_id=row.session_id,
            ttl_s=self._config.share_ttl_seconds,
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with self._store.lock:
            await self._store.execute(
                "INSERT INTO tokens_issued (token_id, session_id, user_key, issued_by, target, "
                "issued_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"t_{uuid.uuid4().hex}", row.session_id, user_key, issued_by, target, now,
                 now + timedelta(seconds=self._config.share_ttl_seconds)),
            )
        return issued.url

    async def _creator_link(self, row: "SessionRow") -> str | None:
        """``share: true``: one-time link for the session creator (§3.4)."""
        try:
            return await self.issue_link(row, user_key=row.created_by, issued_by="alicedev")
        except Exception:  # noqa: BLE001 - a missing link must not break the state change
            _LOG.exception("share link for session %s failed", row.session_id)
            return None

    creator_link = _creator_link

    # --- restart recovery ---------------------------------------------------------------

    async def recover(self) -> None:
        for agent in await self._agents.by_status(AgentRowStatus.CREATING):
            await self._actor.recover_creating(agent)
        for scenario in self._registry().scenarios.values():
            if scenario.exclusive:
                await self._dispatch_next(scenario)
        await self.retry_waiting()

    def visible(self, scenario: Scenario, state_name: str) -> bool:
        try:
            return is_visible(scenario.state(state_name))
        except KeyError:
            return state_name in BUILTIN_STATES and state_name != QUEUED
