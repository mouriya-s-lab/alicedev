"""reply_deliveries repository: at-most-once delivery with idempotent replay.

The claim protocol (ARCHITECTURE §6):

* same ``reply_id`` + same payload digest, already ``sent``  -> replay result
* same ``reply_id`` + different payload digest              -> 409 conflict
* ``reply_id`` in ``claimed`` state older than 60s          -> treat as unknown,
  allow re-claim (may duplicate the platform send; documented)
* otherwise                                                 -> fresh claim

:meth:`claim` MUST be called while holding ``Store.lock`` so the check-then-write
is atomic against a concurrent retry of the same ``reply_id``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alicedev.store.db import Store

_STALE_CLAIM_SECONDS = 60


class ReplyState(str, Enum):
    CLAIMED = "claimed"
    SENT = "sent"
    FAILED = "failed"


class ClaimOutcome(str, Enum):
    FRESH = "fresh"          # caller must perform the platform send
    REPLAY = "replay"        # already sent; return stored platform_message_ids
    CONFLICT = "conflict"    # 409: same reply_id, different payload


@dataclass
class ClaimResult:
    outcome: ClaimOutcome
    platform_message_ids: list[str]


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RepliesRepo:
    def __init__(self, store: "Store") -> None:
        self._store = store

    async def claim(
        self, *, reply_id: str, session_ref: str, msgs: list[str], payload_sha256: str
    ) -> ClaimResult:
        row = await self._store.fetch_one(
            "SELECT payload_sha256, state, platform_message_ids, updated_at "
            "FROM reply_deliveries WHERE reply_id = ?",
            (reply_id,),
        )
        if row is not None:
            existing_digest, state, pmids_json, updated_at = row
            if existing_digest != payload_sha256:
                return ClaimResult(ClaimOutcome.CONFLICT, [])
            if state == ReplyState.SENT.value:
                pmids = json.loads(pmids_json) if pmids_json else []
                return ClaimResult(ClaimOutcome.REPLAY, pmids)
            if state == ReplyState.CLAIMED.value:
                age = (_now_naive() - _coerce_dt(updated_at)).total_seconds()
                if age < _STALE_CLAIM_SECONDS:
                    # A concurrent send is in flight; treat as replay-pending with
                    # no ids yet so the caller returns the same claimed identity.
                    return ClaimResult(ClaimOutcome.REPLAY, [])
                # Stale claim: re-arm it and let the caller retry the send.
                await self._store.execute(
                    "UPDATE reply_deliveries SET updated_at = ? WHERE reply_id = ?",
                    (_now_naive(), reply_id),
                )
                return ClaimResult(ClaimOutcome.FRESH, [])
            # state == failed: allow a fresh retry.
            await self._store.execute(
                "UPDATE reply_deliveries SET state = ?, updated_at = ? WHERE reply_id = ?",
                (ReplyState.CLAIMED.value, _now_naive(), reply_id),
            )
            return ClaimResult(ClaimOutcome.FRESH, [])

        await self._store.execute(
            "INSERT INTO reply_deliveries (reply_id, session_ref, msgs, "
            "payload_sha256, state, platform_message_ids, updated_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, ?)",
            (reply_id, session_ref, json.dumps(msgs), payload_sha256,
             ReplyState.CLAIMED.value, _now_naive()),
        )
        return ClaimResult(ClaimOutcome.FRESH, [])

    async def mark_sent(self, reply_id: str, platform_message_ids: list[str]) -> None:
        await self._store.execute(
            "UPDATE reply_deliveries SET state = ?, platform_message_ids = ?, "
            "updated_at = ? WHERE reply_id = ?",
            (ReplyState.SENT.value, json.dumps(platform_message_ids),
             _now_naive(), reply_id),
        )

    async def mark_failed(self, reply_id: str) -> None:
        await self._store.execute(
            "UPDATE reply_deliveries SET state = ?, updated_at = ? WHERE reply_id = ?",
            (ReplyState.FAILED.value, _now_naive(), reply_id),
        )


def _coerce_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    return datetime.fromisoformat(str(value)).replace(tzinfo=None)
