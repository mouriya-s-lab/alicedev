"""``POST /v1/reply`` enqueue rules (ARCHITECTURE §6 入队规则).

Validation, idempotency and the state transition happen here; the outbox row
and the new session state are written under ``store.lock`` in one step, and the
scheduler performs the entered state's side effects afterwards. The platform is
never touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from alicedev.api.payloads import (
    FileReply,
    ImageReply,
    ImageTemplateReply,
    InvalidPayload,
    ReplyPayload,
    StickerReply,
    TextTemplateReply,
    Transition,
    parse_reply_payload,
    parse_transition,
    payload_digest,
)
from alicedev.dsl.model import AgentState, HumanState, ReplySpec, Scenario, TerminalState, is_visible
from alicedev.reports import ReportError, validate_under_root

if TYPE_CHECKING:
    from alicedev.config import PluginConfig
    from alicedev.outbox.render import OutboxRenderer
    from alicedev.outbox.service import Outbox
    from alicedev.reports import ReportPublisher
    from alicedev.scheduler.engine import Scheduler
    from alicedev.store.agents_repo import AgentsRepo
    from alicedev.store.db import Store
    from alicedev.store.sessions_repo import SessionsRepo

_GENERIC_CARD = "generic_card"
_DEFAULT_SPEC = ReplySpec(kinds=("text",))


@dataclass(frozen=True)
class IntakeResult:
    status: int
    body: Mapping[str, Any]


def _err(status: int, error: str) -> IntakeResult:
    return IntakeResult(status, {"error": error})


class ReplyIntake:
    def __init__(
        self,
        *,
        store: "Store",
        sessions: "SessionsRepo",
        agents: "AgentsRepo",
        outbox: "Outbox",
        reports: "ReportPublisher",
        renderer: "OutboxRenderer",
        scheduler: "Scheduler",
        config: "PluginConfig",
    ) -> None:
        self._store = store
        self._sessions = sessions
        self._agents = agents
        self._outbox = outbox
        self._reports = reports
        self._renderer = renderer
        self._scheduler = scheduler
        self._config = config

    async def handle(self, body: Any) -> IntakeResult:
        if not isinstance(body, Mapping):
            return _err(400, "invalid_payload")
        agent_ref = body.get("agent")
        reply_id = body.get("reply_id")
        msgs = body.get("msgs", [])
        if not isinstance(agent_ref, str) or not isinstance(reply_id, str) or not reply_id:
            return _err(400, "invalid_payload")
        if not isinstance(msgs, list) or not all(isinstance(m, str) for m in msgs):
            return _err(400, "invalid_payload")
        raw_reply = body.get("reply")
        raw_transition = body.get("transition")
        if raw_reply is None and raw_transition is None:
            return _err(400, "invalid_payload")
        try:
            reply = parse_reply_payload(raw_reply) if raw_reply is not None else None
            transition = parse_transition(raw_transition) if raw_transition is not None else None
        except InvalidPayload:
            return _err(400, "invalid_payload")

        agent = await self._agents.get(agent_ref)
        if agent is None:
            return _err(404, "agent_unknown")
        digest = payload_digest({"reply": raw_reply, "transition": raw_transition})

        # Idempotent replay before any state check: a retry after success must
        # replay even though the session has already moved on.
        existing = await self._outbox.repo.get(reply_id)
        if existing is not None:
            return self._replay(existing.payload_sha256, digest, existing.payload)

        async with self._store.lock:
            existing = await self._outbox.repo.get(reply_id)
            if existing is not None:
                return self._replay(existing.payload_sha256, digest, existing.payload)
            row = await self._sessions.get(agent.session_id)
            if row is None or row.state != agent.state:
                return _err(409, "agent_not_current")
            scenario = self._scheduler.scenario(row.scenario)
            if scenario is None:
                return _err(409, "agent_not_current")
            try:
                current = scenario.state(row.state)
            except KeyError:
                return _err(409, "agent_not_current")
            if not isinstance(current, AgentState):
                return _err(409, "agent_not_current")

            checked = self._check_rules(scenario, current, reply, transition)
            if isinstance(checked, IntakeResult):
                return checked
            spec = checked
            if reply is not None:
                problem = self._validate_reply(reply, spec)
                if problem:
                    return _err(400, problem)

            payload: dict[str, Any] | None = None
            report_url: str | None = None
            if reply is not None:
                payload = {
                    "type": "ai",
                    "reply": raw_reply,
                    "marker": f"%{row.no} {row.name}",
                    "max_text_chars": spec.max_text_chars,
                }
                if isinstance(reply, FileReply):
                    try:
                        published = await self._reports.publish(reply.path, session_id=row.session_id)
                    except ReportError as exc:
                        return _err(400, exc.error)
                    payload["published"] = {
                        "basename": published.basename,
                        "path": str(published.published_path),
                        "is_markdown": published.is_markdown,
                        "url": published.url,
                    }
                    report_url = published.url
            else:
                payload = None

            prev_state = row.state
            if transition is not None:
                await self._sessions.set_state(row.session_id, transition.state)
                if transition.data:
                    await self._sessions.merge_data(row.session_id, transition.data)
            outbox_payload = payload if payload is not None else {"type": "transition_only"}
            await self._outbox.insert_ai_locked(
                reply_id=reply_id, chat_key=row.chat_key, session_id=row.session_id,
                agent_ref=agent.agent_ref, msgs=msgs, payload=outbox_payload, payload_sha256=digest,
                deliver=payload is not None,
            )
            await self._agents.touch(agent.agent_ref)

        if transition is not None and payload is not None:
            target = scenario.state(transition.state)
            if getattr(target, "share", False):
                fresh = await self._sessions.get(row.session_id)
                link = await self._scheduler.creator_link(fresh) if fresh else None
                if link:
                    await self._outbox.enqueue_text(row.chat_key, link, session_id=row.session_id)
        self._outbox.wake()
        if transition is not None:
            self._scheduler.schedule_after_reply_transition(row.session_id, prev_state)
        out: dict[str, Any] = {"status": "queued"}
        if report_url:
            out["report_url"] = report_url
        return IntakeResult(202, out)

    @staticmethod
    def _replay(stored: str, digest: str, payload: Mapping[str, Any]) -> IntakeResult:
        if stored != digest:
            return _err(409, "reply_id_conflict")
        out: dict[str, Any] = {"status": "replayed"}
        published = payload.get("published") if isinstance(payload, Mapping) else None
        if isinstance(published, Mapping) and published.get("url"):
            out["report_url"] = published["url"]
        return IntakeResult(202, out)

    def _check_rules(
        self,
        scenario: Scenario,
        current: AgentState,
        reply: ReplyPayload | None,
        transition: Transition | None,
    ) -> ReplySpec | IntakeResult:
        """§6 rules; returns the reply spec to validate against."""
        if transition is None:
            if not current.conversational:
                return _err(400, "transition_required")
            assert current.reply is not None
            return current.reply
        if transition.state not in current.next:
            return _err(400, "transition_not_allowed")
        try:
            target = scenario.state(transition.state)
        except KeyError:
            return _err(400, "transition_not_allowed")
        missing = [k for k in target.data if k not in transition.data]
        if missing:
            return _err(400, "transition_data_missing")
        target_visible = is_visible(target)
        if isinstance(target, (HumanState, TerminalState)) and reply is None:
            return _err(400, "reply_required")
        if reply is not None and not target_visible and not current.conversational:
            return _err(400, "reply_not_visible")
        if target_visible and reply is not None:
            return target.reply or (current.reply if current.conversational else None) or _DEFAULT_SPEC
        if current.reply is not None:
            return current.reply
        return _DEFAULT_SPEC

    def _validate_reply(self, reply: ReplyPayload, spec: ReplySpec) -> str | None:
        kind = reply.kind
        if not spec.allows(kind) and kind != "text":
            return "kind_not_allowed"
        match reply:
            case ImageTemplateReply(template=template):
                if template not in spec.image_templates and template != _GENERIC_CARD:
                    return "template_unknown"
            case TextTemplateReply(template=template):
                if template not in spec.text_templates or not self._renderer.text_template_exists(template):
                    return "template_unknown"
            case StickerReply(sticker=sticker):
                if spec.stickers and sticker not in spec.stickers:
                    return "invalid_payload"
                if not self._renderer.sticker_exists(sticker):
                    return "invalid_payload"
            case ImageReply(paths=paths):
                for path in paths:
                    try:
                        validate_under_root(path, self._config.reports_root)
                    except ReportError as exc:
                        return exc.error
            case _:
                pass
        sticker = getattr(reply, "sticker", None)
        if sticker and not isinstance(reply, StickerReply) and not self._renderer.sticker_exists(sticker):
            return "invalid_payload"
        return None

