"""命令日志。

仓库要求命令类操作在执行前记录完整命令，成功与失败都要记。
日志不含任何凭证：只记 argv、工作目录、退出码与耗时。
写日志失败不中断业务，调用方拿不到异常。
"""

from __future__ import annotations

import time
from pathlib import Path

from .paths import log_file


def _append(line: str) -> None:
    try:
        target = log_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        return


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def started(argv: list[str], cwd: Path | None) -> float:
    where = f" cwd={cwd}" if cwd else ""
    _append(f"{_stamp()} RUN  {argv}{where}")
    return time.monotonic()


def finished(argv: list[str], returncode: int, begin: float) -> None:
    elapsed = time.monotonic() - begin
    head = argv[0] if argv else "?"
    _append(f"{_stamp()} END  {head} rc={returncode} {elapsed:.1f}s")
