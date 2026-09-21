"""Typed access to the ``upgrade_runs`` table.

Upgrade runs are deliberately separate from the normal chat/session mapping.  A
run owns one worktree/workspace and can outlive any of the ordinary paseo
sessions used while the conductor works.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from alicedev.store.db import Store


class UpgradeRunStatus(str, Enum):
    """Persisted lifecycle states from the upgrade-bot contract."""

    ACCEPTED = "accepted"
    CANDIDATE_READY = "candidate_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    DEPLOYING = "deploying"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    MAIN_SYNC_FAILED = "main_sync_failed"


@dataclass(frozen=True)
class UpgradeRunRecord:
    run_id: str
    chat_key: str
    user_key: str
    workspace_ref: str
    status: UpgradeRunStatus
    candidate_commit: str | None
    evidence_refs: dict[str, str]
    created_at: datetime | None
    updated_at: datetime | None


_COLUMNS = (
    "run_id, chat_key, user_key, workspace_ref, status, candidate_commit, "
    "evidence_refs, created_at, updated_at"
)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _evidence_from_db(value: object) -> dict[str, str]:
    if value is None:
        return {}
    raw: object = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("upgrade_runs.evidence_refs is not valid JSON") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("upgrade_runs.evidence_refs must be a JSON object")
    return {str(key): str(item) for key, item in raw.items()}


def _row_to_record(row: tuple) -> UpgradeRunRecord:
    return UpgradeRunRecord(
        run_id=str(row[0]),
        chat_key=str(row[1]),
        user_key=str(row[2]),
        workspace_ref=str(row[3]),
        status=UpgradeRunStatus(str(row[4])),
        candidate_commit=str(row[5]) if row[5] is not None else None,
        evidence_refs=_evidence_from_db(row[6]),
        created_at=row[7],
        updated_at=row[8],
    )


def record_dict(record: UpgradeRunRecord) -> dict[str, object]:
    """Serialize a record for the botctl state API without leaking row tuples."""

    return {
        "run_id": record.run_id,
        "chat_key": record.chat_key,
        "user_key": record.user_key,
        "workspace_ref": record.workspace_ref,
        "status": record.status.value,
        "candidate_commit": record.candidate_commit,
        "evidence_refs": dict(record.evidence_refs),
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


class UpgradeRunsRepo:
    """Single-writer repository for independent upgrade run state."""

    def __init__(self, store: "Store") -> None:
        self._store = store

    async def create(
        self,
        *,
        run_id: str,
        chat_key: str,
        user_key: str,
        workspace_ref: str,
        status: UpgradeRunStatus = UpgradeRunStatus.ACCEPTED,
    ) -> UpgradeRunRecord:
        now = _utc_now_naive()
        await self._store.execute(
            "INSERT INTO upgrade_runs "
            "(run_id, chat_key, user_key, workspace_ref, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, chat_key, user_key, workspace_ref, status.value, now, now),
        )
        record = await self.get(run_id)
        if record is None:  # pragma: no cover - defensive database invariant
            raise RuntimeError(f"upgrade run disappeared after insert: {run_id}")
        return record

    async def get(self, run_id: str) -> UpgradeRunRecord | None:
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM upgrade_runs WHERE run_id = ?", (run_id,)
        )
        return _row_to_record(row) if row else None

    async def latest_for_chat(self, chat_key: str) -> UpgradeRunRecord | None:
        row = await self._store.fetch_one(
            f"SELECT {_COLUMNS} FROM upgrade_runs "
            "WHERE chat_key = ? ORDER BY created_at DESC, run_id DESC LIMIT 1",
            (chat_key,),
        )
        return _row_to_record(row) if row else None

    async def list_for_chat(self, chat_key: str) -> list[UpgradeRunRecord]:
        rows = await self._store.fetch_all(
            f"SELECT {_COLUMNS} FROM upgrade_runs "
            "WHERE chat_key = ? ORDER BY created_at DESC, run_id DESC",
            (chat_key,),
        )
        return [_row_to_record(row) for row in rows]

    async def delete(self, run_id: str) -> bool:
        async with self._store.lock:
            before = await self.get(run_id)
            if before is None:
                return False
            await self._store.execute("DELETE FROM upgrade_runs WHERE run_id = ?", (run_id,))
        return True

    async def transition(
        self,
        run_id: str,
        status: UpgradeRunStatus,
        *,
        from_statuses: tuple[UpgradeRunStatus, ...] | None = None,
    ) -> bool:
        """Move a run once, optionally guarding the expected predecessor state."""

        now = _utc_now_naive()
        params: list[object] = [status.value, now, run_id]
        predicate = "run_id = ?"
        if from_statuses:
            predicate += " AND status IN (" + ",".join("?" for _ in from_statuses) + ")"
            params.extend(item.value for item in from_statuses)
        async with self._store.lock:
            row = await self._store.fetch_one(
                f"SELECT 1 FROM upgrade_runs WHERE {predicate}", tuple(params[2:])
            )
            if row is None:
                return False
            await self._store.execute(
                f"UPDATE upgrade_runs SET status = ?, updated_at = ? WHERE {predicate}",
                tuple(params),
            )
        return True

    async def set_status(self, run_id: str, status: UpgradeRunStatus) -> bool:
        return await self.transition(run_id, status)

    async def mark_candidate_ready(
        self,
        run_id: str,
        *,
        commit: str,
        evidence_refs: Mapping[str, str],
    ) -> bool:
        """Persist candidate evidence and enter the approval-ready state."""
        now = _utc_now_naive()
        encoded = json.dumps(dict(evidence_refs), ensure_ascii=False, sort_keys=True)
        async with self._store.lock:
            row = await self._store.fetch_one(
                "SELECT 1 FROM upgrade_runs WHERE run_id = ?", (run_id,)
            )
            if row is None:
                return False
            await self._store.execute(
                "UPDATE upgrade_runs SET status = ?, candidate_commit = ?, evidence_refs = ?, "
                "updated_at = ? WHERE run_id = ?",
                (
                    UpgradeRunStatus.CANDIDATE_READY.value,
                    commit,
                    encoded,
                    now,
                    run_id,
                ),
            )
        return True

    async def mark_awaiting_approval(self, run_id: str) -> bool:
        return await self.transition(
            run_id,
            UpgradeRunStatus.AWAITING_APPROVAL,
            from_statuses=(UpgradeRunStatus.CANDIDATE_READY,),
        )

    async def mark_deploying(self, run_id: str) -> bool:
        return await self.transition(
            run_id,
            UpgradeRunStatus.DEPLOYING,
            from_statuses=(
                UpgradeRunStatus.CANDIDATE_READY,
                UpgradeRunStatus.AWAITING_APPROVAL,
            ),
        )

    async def mark_deploy_result(self, run_id: str, outcome: str) -> bool:
        status_by_outcome = {
            "active": UpgradeRunStatus.ACTIVE,
            "rolled_back": UpgradeRunStatus.ROLLED_BACK,
            "rollback_failed": UpgradeRunStatus.FAILED,
        }
        try:
            status = status_by_outcome[outcome]
        except KeyError as exc:
            raise ValueError(f"unknown deploy outcome: {outcome!r}") from exc
        return await self.transition(
            run_id,
            status,
            from_statuses=(UpgradeRunStatus.DEPLOYING, UpgradeRunStatus.ACCEPTED),
        )

    async def mark_failed(self, run_id: str) -> bool:
        return await self.transition(run_id, UpgradeRunStatus.FAILED)

    async def mark_main_sync_failed(self, run_id: str) -> bool:
        return await self.transition(run_id, UpgradeRunStatus.MAIN_SYNC_FAILED)

    async def reject(self, run_id: str) -> bool:
        """Map an explicit rejection to the contract's terminal ``failed`` state."""

        return await self.transition(
            run_id,
            UpgradeRunStatus.FAILED,
            from_statuses=(
                UpgradeRunStatus.ACCEPTED,
                UpgradeRunStatus.CANDIDATE_READY,
                UpgradeRunStatus.AWAITING_APPROVAL,
            ),
        )

    async def expire(self, run_id: str) -> bool:
        """Expire a pending run without adding a non-contract status value."""

        return await self.reject(run_id)
