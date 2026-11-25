"""外部二进制探测。

两个上游工具的发行包名带版本号，直接跑 ``kuake`` 常常不存在。
本模块按固定顺序找：显式配置 → PATH 里的裸名 → 所有探测目录里带版本号的文件名。
带版本号时全局取最高版本，不让 PATH 里一个旧副本压过 ``~/.local/bin`` 里的新版本。
找不到就返回 ``None``，由调用方决定是报错还是禁用功能。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .paths import bin_dir

KUAKE = "kuake"
GOQUARK = "goquark"
SQLITE3 = "sqlite3"

# 每个工具的候选文件名。含 * 的按 glob 匹配，同分时取版本号最高者。
CANDIDATES: dict[str, tuple[str, ...]] = {
    KUAKE: (KUAKE, "kuake-v*"),
    GOQUARK: (GOQUARK, "goquark-*", "goquark-v*"),
    SQLITE3: (SQLITE3,),
}

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class Tool:
    name: str
    path: Path


def search_dirs() -> list[Path]:
    """探测目录：项目 ``bin/`` → PATH 各项 → ``~/.local/bin``，保序去重。

    项目 ``bin/`` 排最前：install-tools.sh 装在那里的就是本项目的工具，
    不该被 PATH 里某个同名旧副本顶掉。
    """
    dirs: list[Path] = [bin_dir()]
    for raw in os.environ.get("PATH", "").split(os.pathsep):
        if raw:
            dirs.append(Path(raw))
    dirs.append(Path("~/.local/bin").expanduser())
    seen: set[Path] = set()
    unique: list[Path] = []
    for item in dirs:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _version_key(name: str) -> tuple[int, int, int]:
    matched = _VERSION_RE.search(name)
    if not matched:
        return (0, 0, 0)
    return tuple(int(part) for part in matched.groups())  # type: ignore[return-value]


def resolve(name: str, explicit: str = "") -> Path | None:
    """按约定顺序解析二进制路径。"""
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.parent == Path("."):
            # 只写了名字，例如配置里的默认值 "sqlite3"
            located = shutil.which(explicit)
            return Path(located) if located else None
        return candidate if _executable(candidate) else None

    # 裸名按目录顺序取第一个：项目 bin 因此压过 PATH
    for directory in search_dirs():
        candidate = directory / name
        if _executable(candidate):
            return candidate

    # 只剩带版本号的文件名时，全局取最高版本
    found: list[Path] = []
    for directory in search_dirs():
        for pattern in CANDIDATES.get(name, (name,)):
            if "*" in pattern:
                found.extend(item for item in directory.glob(pattern) if _executable(item))
    if found:
        found.sort(key=lambda item: (_version_key(item.name), str(item)), reverse=True)
        return found[0]

    located = shutil.which(name)
    return Path(located) if located else None


def detect(name: str, explicit: str = "") -> Tool | None:
    path = resolve(name, explicit)
    return Tool(name=name, path=path) if path else None


def probe_version(tool: Tool) -> str:
    """取版本号。两个工具都认 ``version`` 子命令；取不到不作为失败。"""
    try:
        completed = subprocess.run(
            [str(tool.path), "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (completed.stdout or "") + (completed.stderr or "")
    matched = _VERSION_RE.search(text)
    if matched:
        return matched.group(0)
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first[:40]
