"""操作历史。

记两样东西：走过哪些目录、传过哪些东西。两样都按最近使用排序，
常用的往上浮。历史坏了不影响主流程：读不出来就当空的，并留一条日志。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .paths import cache_root

DIR_LIMIT = 40
TRANSFER_LIMIT = 20
PANE_REMOTE = "remote"
PANE_LOCAL = "local"
KIND_UPLOAD = "upload"
KIND_DOWNLOAD = "download"


@dataclass
class DirEntry:
    pane: str
    path: str
    count: int = 1
    last: float = 0.0


@dataclass
class TransferEntry:
    kind: str
    local: str
    remote_dir: str
    name: str
    last: float = 0.0


@dataclass
class History:
    """内存里的历史。写盘只在 :meth:`save`。"""

    path: Path
    dirs: list[DirEntry] = field(default_factory=list)
    transfers: list[TransferEntry] = field(default_factory=list)
    broken: str = ""

    @classmethod
    def open(cls, path: Path | None = None) -> History:
        target = path or (cache_root() / "history.json")
        history = cls(path=target)
        if not target.is_file():
            return history
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            history.broken = str(exc)
            return history
        if not isinstance(payload, dict):
            history.broken = "顶层不是对象"
            return history
        history.dirs = _parse_dirs(payload.get("dirs"))
        history.transfers = _parse_transfers(payload.get("transfers"))
        return history

    def save(self) -> None:
        payload = {
            "dirs": [asdict(entry) for entry in self.dirs[:DIR_LIMIT]],
            "transfers": [asdict(entry) for entry in self.transfers[:TRANSFER_LIMIT]],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            self.broken = str(exc)

    # ---------------- 记录 ----------------

    def record_dir(self, pane: str, path: str) -> None:
        if pane not in (PANE_REMOTE, PANE_LOCAL) or not path:
            return
        now = time.time()
        for entry in self.dirs:
            if entry.pane == pane and entry.path == path:
                entry.count += 1
                entry.last = now
                break
        else:
            self.dirs.append(DirEntry(pane=pane, path=path, count=1, last=now))
        self.dirs.sort(key=lambda entry: (entry.count, entry.last), reverse=True)
        del self.dirs[DIR_LIMIT:]

    def record_transfer(self, kind: str, local: str, remote_dir: str, name: str) -> None:
        if kind not in (KIND_UPLOAD, KIND_DOWNLOAD) or not name:
            return
        now = time.time()
        self.transfers = [
            entry
            for entry in self.transfers
            if not (
                entry.kind == kind
                and entry.local == local
                and entry.remote_dir == remote_dir
                and entry.name == name
            )
        ]
        self.transfers.insert(
            0,
            TransferEntry(kind=kind, local=local, remote_dir=remote_dir, name=name, last=now),
        )
        del self.transfers[TRANSFER_LIMIT:]

    # ---------------- 读取 ----------------

    def recent_dirs(self) -> list[DirEntry]:
        return sorted(self.dirs, key=lambda entry: (entry.count, entry.last), reverse=True)

    def recent_transfers(self) -> list[TransferEntry]:
        return sorted(self.transfers, key=lambda entry: entry.last, reverse=True)


def _parse_dirs(raw: object) -> list[DirEntry]:
    if not isinstance(raw, list):
        return []
    picked: list[DirEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        pane = item.get("pane")
        path = item.get("path")
        if pane not in (PANE_REMOTE, PANE_LOCAL) or not isinstance(path, str) or not path:
            continue
        count = item.get("count")
        last = item.get("last")
        picked.append(
            DirEntry(
                pane=pane,
                path=path,
                count=count if isinstance(count, int) and count > 0 else 1,
                last=float(last) if isinstance(last, (int, float)) else 0.0,
            )
        )
    return picked[:DIR_LIMIT]


def _parse_transfers(raw: object) -> list[TransferEntry]:
    if not isinstance(raw, list):
        return []
    picked: list[TransferEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        name = item.get("name")
        local = item.get("local")
        remote_dir = item.get("remote_dir")
        if kind not in (KIND_UPLOAD, KIND_DOWNLOAD):
            continue
        if not all(isinstance(value, str) for value in (name, local, remote_dir)):
            continue
        last = item.get("last")
        picked.append(
            TransferEntry(
                kind=kind,
                local=str(local),
                remote_dir=str(remote_dir),
                name=str(name),
                last=float(last) if isinstance(last, (int, float)) else 0.0,
            )
        )
    return picked[:TRANSFER_LIMIT]
