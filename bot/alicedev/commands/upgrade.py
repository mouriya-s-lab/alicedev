"""``/升级bot`` command and the bot-to-conductor shim contract.

The real conductor launcher lives outside the bot process.  Wave 1 keeps a
small no-op implementation here so the command, state, and callback surfaces
can be exercised in an isolated bot without starting paseo or touching
production.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Protocol

from alicedev.commands.registry import CommandSpec
from alicedev.store.upgrade_runs_repo import (
    UpgradeRunRecord,
    UpgradeRunStatus,
    UpgradeRunsRepo,
)

if TYPE_CHECKING:
    from alicedev.commands.context import CommandContext, Services
    from alicedev.commands.registry import CommandRegistry


_LOG = logging.getLogger("alicedev.commands.upgrade")


class UpgradeConductor(Protocol):
    """CLI/shim interface between bot and paseo conductor (§3)."""

    async def start(self, *, prompt: str, chat_key: str, user_key: str) -> None:
        """Start a conductor for one upgrade request."""

    async def approve(self, *, run_id: str, commit: str) -> None:
        """Approve exactly one candidate commit."""

    async def reject(self, *, run_id: str) -> None:
        """Reject one pending run."""


class StubUpgradeConductor:
    """Isolated Wave 1 implementation; the external paseo shim replaces this."""

    async def start(self, *, prompt: str, chat_key: str, user_key: str) -> None:
        _LOG.info(
            "upgrade conductor start stub: prompt_chars=%d chat=%s user=%s",
            len(prompt),
            chat_key,
            user_key,
        )

    async def approve(self, *, run_id: str, commit: str) -> None:
        _LOG.info("upgrade conductor approve stub: run=%s commit=%s", run_id, commit)

    async def reject(self, *, run_id: str) -> None:
        _LOG.info("upgrade conductor reject stub: run=%s", run_id)


def _repo(ctx: "CommandContext") -> UpgradeRunsRepo:
    return UpgradeRunsRepo(ctx.services.store)


def _new_run_id() -> str:
    return "ur_" + uuid.uuid4().hex


def _status_text(record: UpgradeRunRecord) -> str:
    commit = f"\n候选 commit：{record.candidate_commit}" if record.candidate_commit else ""
    return f"升级运行 {record.run_id}\n状态：{record.status.value}{commit}"


def _parse_subcommand(args: str) -> tuple[str, list[str]]:
    parts = args.strip().split()
    if not parts:
        return "start", []
    command = parts[0].lower()
    if command in {"status", "approve", "reject", "expire"}:
        return command, parts[1:]
    return "start", parts


async def _handle_status(ctx: "CommandContext", args: list[str]) -> None:
    repo = _repo(ctx)
    if args:
        record = await repo.get(args[0])
    else:
        record = await repo.latest_for_chat(ctx.chat_key)
    if record is None or record.chat_key != ctx.chat_key:
        await ctx.reply_text("当前聊天没有找到对应的升级运行。")
        return
    await ctx.reply_text(_status_text(record))


async def _handle_start(ctx: "CommandContext", prompt_parts: list[str]) -> None:
    prompt = " ".join(prompt_parts).strip()
    if not prompt:
        await ctx.reply_text("用法：/升级bot <要修复的问题>")
        return

    run_id = _new_run_id()
    workspace_ref = f"upgrade/{run_id}"
    repo = _repo(ctx)
    await repo.create(
        run_id=run_id,
        chat_key=ctx.chat_key,
        user_key=ctx.user_key,
        workspace_ref=workspace_ref,
    )
    conductor = ctx.services.conductor or StubUpgradeConductor()
    try:
        await conductor.start(prompt=prompt, chat_key=ctx.chat_key, user_key=ctx.user_key)
    except Exception:  # noqa: BLE001 - keep the accepted row auditable
        _LOG.exception("upgrade conductor start failed: run=%s", run_id)
        await repo.mark_failed(run_id)
        await ctx.reply_text(f"升级运行 {run_id} 启动失败，已记录为 failed。")
        return
    await ctx.reply_text(f"已接受升级请求，运行 {run_id} 已进入 accepted。")


async def _handle_approve(ctx: "CommandContext", args: list[str]) -> None:
    if not args:
        await ctx.reply_text("用法：/升级bot approve <run_id> [candidate_commit]")
        return
    run_id = args[0]
    repo = _repo(ctx)
    record = await repo.get(run_id)
    if record is None or record.chat_key != ctx.chat_key:
        await ctx.reply_text("未找到这个聊天中的升级运行。")
        return
    if record.status not in (
        UpgradeRunStatus.CANDIDATE_READY,
        UpgradeRunStatus.AWAITING_APPROVAL,
    ):
        await ctx.reply_text(f"运行 {run_id} 当前状态为 {record.status.value}，不能批准。")
        return
    candidate = record.candidate_commit
    if not candidate:
        await ctx.reply_text("该运行还没有候选 commit。")
        return
    if len(args) > 1 and args[1] != candidate:
        await ctx.reply_text("批准失败：candidate commit 与记录不一致。")
        return
    if len(args) > 2:
        await ctx.reply_text("用法：/升级bot approve <run_id> [candidate_commit]")
        return

    conductor = ctx.services.conductor or StubUpgradeConductor()
    try:
        await conductor.approve(run_id=run_id, commit=candidate)
    except Exception:  # noqa: BLE001 - preserve candidate for retry
        _LOG.exception("upgrade approve failed: run=%s", run_id)
        await ctx.reply_text("批准发送失败，候选仍可重试。")
        return
    if not await repo.mark_deploying(run_id):
        await ctx.reply_text("批准未生效：运行状态已变化，请重新查询。")
        return
    await ctx.reply_text(f"已批准运行 {run_id} 的 candidate commit {candidate}，进入 deploying。")


async def _handle_reject(ctx: "CommandContext", args: list[str]) -> None:
    if len(args) != 1:
        await ctx.reply_text("用法：/升级bot reject <run_id>")
        return
    run_id = args[0]
    repo = _repo(ctx)
    record = await repo.get(run_id)
    if record is None or record.chat_key != ctx.chat_key:
        await ctx.reply_text("未找到这个聊天中的升级运行。")
        return
    if record.status not in (
        UpgradeRunStatus.ACCEPTED,
        UpgradeRunStatus.CANDIDATE_READY,
        UpgradeRunStatus.AWAITING_APPROVAL,
    ):
        await ctx.reply_text(f"运行 {run_id} 当前状态为 {record.status.value}，不能拒绝。")
        return
    conductor = ctx.services.conductor or StubUpgradeConductor()
    try:
        await conductor.reject(run_id=run_id)
    except Exception:  # noqa: BLE001
        _LOG.exception("upgrade reject failed: run=%s", run_id)
        await ctx.reply_text("拒绝发送失败，运行仍保持原状态。")
        return
    if not await repo.reject(run_id):
        await ctx.reply_text("拒绝未生效：运行状态已变化，请重新查询。")
        return
    await ctx.reply_text(f"已拒绝运行 {run_id}，状态为 failed。")


async def _handle_expire(ctx: "CommandContext", args: list[str]) -> None:
    """Administrative/manual expiry path; expiry is represented as ``failed``."""

    if len(args) != 1:
        await ctx.reply_text("用法：/升级bot expire <run_id>")
        return
    record = await _repo(ctx).get(args[0])
    if record is None or record.chat_key != ctx.chat_key:
        await ctx.reply_text("未找到这个聊天中的升级运行。")
        return
    if not await _repo(ctx).expire(args[0]):
        await ctx.reply_text("该运行已经结束，无法 expire。")
        return
    await ctx.reply_text(f"运行 {args[0]} 已过期，状态为 failed。")


async def _handle_upgrade(ctx: "CommandContext") -> None:
    if not ctx.is_admin:
        await ctx.reply_text("只有管理员可以使用 /升级bot。")
        return
    command, args = _parse_subcommand(ctx.args)
    if command == "start":
        await _handle_start(ctx, args)
    elif command == "status":
        await _handle_status(ctx, args)
    elif command == "approve":
        await _handle_approve(ctx, args)
    elif command == "reject":
        await _handle_reject(ctx, args)
    elif command == "expire":
        await _handle_expire(ctx, args)
    else:  # pragma: no cover - parser is exhaustive
        raise AssertionError(f"unhandled upgrade command: {command}")


def register(registry: "CommandRegistry", services: "Services") -> None:
    del services
    registry.register(
        CommandSpec(
            name="升级bot",
            aliases=("upgradebot", "upgrade-bot"),
            description="启动或审批 alicedev bot 自升级运行",
            admin_only=True,
            handler=_handle_upgrade,
        )
    )
