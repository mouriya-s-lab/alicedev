"""fixed-main alignment (ARCHITECTURE §7 派发): fetch + fast-forward only.

Same fail-closed rules as ``tools/mainsync align``, executed with git inside the
paseo container (the fixed-main lives on its ``/workspace`` volume).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from alicedev.paseo.control import PaseoControl


@dataclass(frozen=True)
class Aligned:
    sha: str


@dataclass(frozen=True)
class SyncFailed:
    reason: str


SyncResult = Union[Aligned, SyncFailed]


async def align(paseo: PaseoControl, repo: str) -> SyncResult:
    async def out(*args: str) -> tuple[bool, str]:
        result = await paseo.git(repo, *args)
        return result.ok, result.stdout.strip()

    ok, inside = await out("rev-parse", "--is-inside-work-tree")
    if not ok or inside != "true":
        return SyncFailed(f"{repo} 不是 git 工作区")
    ok, bare = await out("rev-parse", "--is-bare-repository")
    if not ok or bare != "false":
        return SyncFailed(f"{repo} 是 bare 仓库")
    ok, branch = await out("symbolic-ref", "--quiet", "--short", "HEAD")
    if not ok or branch != "main":
        return SyncFailed(f"fixed-main 当前分支不是 main（{branch or 'detached'}）")
    ok, origin = await out("remote", "get-url", "origin")
    if not ok or not origin:
        return SyncFailed("fixed-main 没有 origin")
    ok, upstream = await out("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if not ok or upstream != "origin/main":
        return SyncFailed(f"fixed-main 的 upstream 不是 origin/main（{upstream or '无'}）")
    ok, status = await out("status", "--porcelain=v1", "--untracked-files=all")
    if not ok or status:
        return SyncFailed("fixed-main 有未提交或未跟踪的改动")
    for marker in ("MERGE_HEAD", "REBASE_HEAD"):
        ok, _ = await out("rev-parse", "--verify", "--quiet", marker)
        if ok:
            return SyncFailed(f"fixed-main 有进行中的 {marker}")
    ok, _ = await out("fetch", "--prune", "origin", "main")
    if not ok:
        return SyncFailed("git fetch origin main 失败")
    ok_base, merge_base = await out("merge-base", "HEAD", "origin/main")
    ok_head, head = await out("rev-parse", "HEAD")
    if not (ok_base and ok_head) or merge_base != head:
        return SyncFailed("fixed-main 的 main 领先或偏离 origin/main（不能快进）")
    ok, _ = await out("merge", "--ff-only", "origin/main")
    if not ok:
        return SyncFailed("git merge --ff-only origin/main 失败")
    ok, sha = await out("rev-parse", "HEAD")
    if not ok or not sha:
        return SyncFailed("无法读取对齐后的 HEAD")
    return Aligned(sha)
