"""Report/file publishing (ARCHITECTURE §6, ``kind:"file"``).

Validates that a path lives under ``REPORTS_ROOT`` with no symlink escape, copies
it atomically into ``REPORTS_ROOT/_published/<report_id>/<basename>``, records it,
and returns a public bearer URL for ``.md`` (128-bit ``report_id``).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from alicedev.ids import new_report_id

if TYPE_CHECKING:
    from alicedev.config import PluginConfig
    from alicedev.store.db import Store

_PUBLISHED_DIR = "_published"


class ReportError(ValueError):
    """File publish failed with a specific §6 error code."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


@dataclass(frozen=True)
class PublishedFile:
    report_id: str
    basename: str
    published_path: Path
    is_markdown: bool
    url: str | None


class ReportPublisher:
    def __init__(self, *, store: "Store", config: "PluginConfig") -> None:
        self._store = store
        self._config = config

    async def publish(self, path: str) -> PublishedFile:
        root = self._config.reports_root.resolve()
        real = Path(os.path.realpath(path))
        # realpath is under REPORTS_ROOT
        try:
            real.relative_to(root)
        except ValueError:
            raise ReportError("path_outside_root")
        # published area is not itself republishable
        published_root = root / _PUBLISHED_DIR
        if published_root in real.parents or real == published_root:
            raise ReportError("path_outside_root")
        # must be a regular file, and no path segment may be a symlink
        if not real.is_file() or real.is_symlink():
            raise ReportError("file_not_regular")
        st = os.lstat(real)
        if not _is_regular(st.st_mode):
            raise ReportError("file_not_regular")
        _assert_no_symlink_segments(real, root)

        basename = real.name
        report_id = new_report_id()
        dest_dir = published_root / report_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / basename
        _atomic_copy(real, dest)

        await self._store.execute(
            "INSERT INTO reports (report_id, session_ref, source_path, published_path) "
            "VALUES (?, ?, ?, ?)",
            (report_id, None, str(real), str(dest)),
        )

        is_md = basename.lower().endswith(".md")
        url: str | None = None
        if is_md and self._config.public_base_url:
            base = self._config.public_base_url.rstrip("/")
            url = f"{base}/_alicedev/r/{report_id}/{basename}"
        return PublishedFile(
            report_id=report_id, basename=basename, published_path=dest,
            is_markdown=is_md, url=url,
        )


def _is_regular(mode: int) -> bool:
    import stat

    return stat.S_ISREG(mode)


def _assert_no_symlink_segments(target: Path, root: Path) -> None:
    current = target
    while current != root and current != current.parent:
        if current.is_symlink():
            raise ReportError("file_not_regular")
        current = current.parent


def _atomic_copy(src: Path, dest: Path) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
