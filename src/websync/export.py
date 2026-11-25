"""SQLite 快照导出。

用 ``.backup`` 而不是复制文件：``.backup`` 读一致快照，复制在 WAL 模式下
可能拿到撕裂副本或漏掉未 checkpoint 的事务。
"""

from __future__ import annotations

import re
from pathlib import Path

from .errors import KIND_TRANSFER, WebSyncError
from .transfer import CancelToken, run_command

UNSAFE_NAME_RE = re.compile(r"[\s'\"\\]")
SQLITE_HEADER = b"SQLite format 3\x00"


def is_sqlite(path: Path) -> bool:
    """看文件头 16 字节。不是数据库就按普通文件传，不做快照。"""
    try:
        with path.open("rb") as handle:
            return handle.read(16) == SQLITE_HEADER
    except OSError:
        return False


def integrity_check(sqlite3: Path, database: Path, cancel: CancelToken | None = None) -> str:
    result = run_command(
        [str(sqlite3), str(database), "PRAGMA integrity_check;"],
        timeout=600.0,
        cancel=cancel,
    )
    return result.stdout.strip()


def export_snapshot(
    sqlite3: Path,
    source: Path,
    destination: Path,
    cancel: CancelToken | None = None,
) -> Path:
    """导出一份快照到 ``destination``。先写 ``.part``，校验通过再改名。"""
    source = source.expanduser()
    if not source.is_absolute():
        source = source.resolve()
    if not source.is_file():
        raise WebSyncError(KIND_TRANSFER, f"源库不存在：{source}")
    if UNSAFE_NAME_RE.search(destination.name):
        raise WebSyncError(KIND_TRANSFER, f"目标文件名含不安全字符：{destination.name}")

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WebSyncError(
            KIND_TRANSFER,
            f"暂存目录不可写：{destination.parent}",
            problems=[str(exc), "可用 XDG_CACHE_HOME 指到可写位置"],
        ) from exc

    part = destination.with_name(destination.name + ".part")
    if part.exists():
        part.unlink()

    result = run_command(
        [str(sqlite3), str(source), f".backup {part.name}"],
        cwd=destination.parent,
        timeout=600.0,
        cancel=cancel,
    )
    if result.returncode != 0:
        raise WebSyncError(
            KIND_TRANSFER,
            f"导出失败：{source}",
            problems=[result.tail],
        )
    if not part.is_file() or part.stat().st_size == 0:
        raise WebSyncError(KIND_TRANSFER, f"导出产物为空：{part}")

    verdict = integrity_check(sqlite3, part, cancel)
    if verdict != "ok":
        part.unlink(missing_ok=True)
        raise WebSyncError(KIND_TRANSFER, f"快照未通过完整性检查：{part}", problems=[verdict[:400]])

    part.replace(destination)
    return destination
