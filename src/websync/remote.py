"""远端目录读取。

列表一律走 ``kuake list --stream``。不用 ``goquark ls``：它只回 100 条，
且不报截断，静默丢数据。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .errors import KIND_TRANSFER, WebSyncError
from .naming import SHA256_SUFFIX
from .tools import Tool
from .transfer import CancelToken, run_command

LIST_TIMEOUT = 120.0


@dataclass(frozen=True)
class RemoteEntry:
    """远端一条目录项。目录的 size 固定是 0。"""

    name: str
    size: int
    is_dir: bool = False
    mtime: float = 0.0


def _entries_from_payload(payload: object) -> list[RemoteEntry]:
    if isinstance(payload, list):
        entries: list[RemoteEntry] = []
        for item in payload:
            entries.extend(_entries_from_payload(item))
        return entries
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("list"), list):
        return _entries_from_payload(payload["list"])
    if "data" in payload:
        return _entries_from_payload(payload["data"])
    name = payload.get("file_name") or payload.get("name")
    if not isinstance(name, str) or not name:
        return []
    size = payload.get("size")
    mtime = payload.get("mtime")
    return [
        RemoteEntry(
            name=name,
            size=size if isinstance(size, int) else 0,
            is_dir=bool(payload.get("dir")),
            mtime=float(mtime) if isinstance(mtime, (int, float)) else 0.0,
        )
    ]


def parse_listing(stdout: str) -> list[RemoteEntry]:
    """解析 ``kuake list`` 的输出。``--stream`` 是行 JSON，兼容整体信封。"""
    text = stdout.strip()
    if not text:
        return []

    entries: list[RemoteEntry] = []
    parsed_all = True
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            parsed_all = False
            break
        entries.extend(_entries_from_payload(payload))
    if parsed_all and entries:
        return entries

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WebSyncError(
            KIND_TRANSFER,
            "无法解析远端目录列表",
            problems=[str(exc), text[:200]],
        ) from exc
    return _entries_from_payload(payload)


def list_dir(
    tool: Tool,
    remote_dir: str,
    env: dict[str, str] | None = None,
    cancel: CancelToken | None = None,
) -> list[RemoteEntry]:
    """读远端目录。

    退出码非零一律报错。部分列表比没有列表更危险：预检、字节数核对、
    校验文件存在性都建在它上面，少一条就会被当成「远端没有」。
    """
    result = run_command(
        [str(tool.path), "list", remote_dir, "--stream"],
        env=env,
        timeout=LIST_TIMEOUT,
        cancel=cancel,
    )
    if result.returncode != 0:
        raise WebSyncError(
            KIND_TRANSFER,
            f"读取远端目录失败：{remote_dir}",
            problems=[result.tail],
        )
    return parse_listing(result.stdout)


def sorted_entries(entries: list[RemoteEntry]) -> list[RemoteEntry]:
    """给界面用的顺序：目录在前，文件在后。

    目录按名字升序，文件按时间倒序（最新在最上面）。
    ``.sha256`` 是本工具自己的校验旁文件，不展示给用户。
    """
    entries = [item for item in entries if not item.name.endswith(SHA256_SUFFIX)]
    directories = sorted((item for item in entries if item.is_dir), key=lambda item: item.name)
    files = sorted(
        (item for item in entries if not item.is_dir),
        key=lambda item: (item.mtime, item.name),
        reverse=True,
    )
    return [*directories, *files]


def find(entries: list[RemoteEntry], name: str) -> RemoteEntry | None:
    for item in entries:
        if item.name == name:
            return item
    return None


def join(remote_dir: str, name: str) -> str:
    """拼远端路径。根目录是 ``/``。"""
    return f"{remote_dir.rstrip('/')}/{name}"


def parent(remote_dir: str) -> str:
    """上一级。已经在根就返回根。"""
    trimmed = remote_dir.rstrip("/")
    if not trimmed:
        return "/"
    head, _, _tail = trimmed.rpartition("/")
    return head or "/"
