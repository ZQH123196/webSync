"""子进程执行与进度解析。

两个上游工具的进度都只写在 stderr，且用 ``\\r`` 覆盖刷新。
本模块把两条流都收集起来，并按行切分交给回调。
所有外部命令都从这里过，日志因此只有一处。
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import log as command_log
from .errors import KIND_TRANSFER, Cancelled, WebSyncError

KUAKE_UPLOAD_RE = re.compile(
    r"上传进度[:：]\s*(?P<pct>\d+)%"
    r"(?:\s*\|\s*速度[:：]\s*(?P<speed>[^|\r\n]*))?"
    r"(?:\s*\|\s*剩余[:：]\s*(?P<eta>[^|\r\n]*))?"
)
GOQUARK_RE = re.compile(
    r"progress\s+(?P<pct>[\d.]+)%(?:\s+(?P<speed>[\d.]+\s*[KMG]iB/s))?"
)

DEFAULT_TIMEOUT = 300.0
LINE_SPLIT_RE = re.compile(r"[\r\n]+")


@dataclass(frozen=True)
class Progress:
    percent: float = field(default=0.0)
    speed: str = ""
    eta: str = ""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined(self) -> str:
        return f"{self.stdout}\n{self.stderr}"

    @property
    def tail(self) -> str:
        return self.combined.strip()[-400:] or f"退出码 {self.returncode}"


def parse_progress(line: str) -> Progress | None:
    """从一行输出里取进度。认不出就返回 ``None``。"""
    matched = KUAKE_UPLOAD_RE.search(line)
    if matched:
        return Progress(
            percent=float(matched.group("pct")),
            speed=(matched.group("speed") or "").strip(),
            eta=(matched.group("eta") or "").strip(),
        )
    matched = GOQUARK_RE.search(line)
    if matched:
        return Progress(
            percent=float(matched.group("pct")),
            speed=(matched.group("speed") or "").strip(),
        )
    return None


class CancelToken:
    """中断句柄。传输期间绑定当前进程，取消时直接杀掉。"""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def bind(self, proc: subprocess.Popen[bytes] | None) -> None:
        self._proc = proc

    def cancel(self) -> None:
        self._cancelled = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()


def _decode(chunk: bytes) -> str:
    return chunk.decode("utf-8", errors="replace")


def run_command(
    argv: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float | None = DEFAULT_TIMEOUT,
    on_progress: Callable[[Progress], None] | None = None,
    cancel: CancelToken | None = None,
) -> CommandResult:
    """执行命令，边跑边回报进度。退出码非零不抛错，由调用方判定。"""
    args = list(argv)
    if cancel is not None and cancel.cancelled:
        # 已经中断过就不要再起进程，否则会在用户以为停下之后继续上传
        raise Cancelled()

    begin = command_log.started(args, cwd)
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=env,
            bufsize=0,
        )
    except OSError as exc:
        command_log.finished(args, -1, begin)
        raise WebSyncError(KIND_TRANSFER, f"无法启动命令：{args[0]}", problems=[str(exc)]) from exc

    if cancel is not None:
        cancel.bind(proc)

    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []

    def pump_stderr() -> None:
        assert proc.stderr is not None
        buffer = ""
        while True:
            chunk = proc.stderr.read(4096)
            if not chunk:
                break
            err_chunks.append(chunk)
            if on_progress is None:
                continue
            buffer += _decode(chunk)
            parts = LINE_SPLIT_RE.split(buffer)
            buffer = parts.pop()
            for part in parts:
                parsed = parse_progress(part)
                if parsed is not None:
                    on_progress(parsed)
        if on_progress is not None and buffer:
            parsed = parse_progress(buffer)
            if parsed is not None:
                on_progress(parsed)

    reader = threading.Thread(target=pump_stderr, daemon=True)
    reader.start()

    returncode = -1
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            out_chunks.append(chunk)
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise WebSyncError(KIND_TRANSFER, f"命令超时：{' '.join(args[:2])}") from exc
    finally:
        reader.join(timeout=5)
        if cancel is not None:
            cancel.bind(None)
        command_log.finished(args, returncode, begin)

    result = CommandResult(
        argv=tuple(args),
        returncode=returncode,
        stdout=b"".join(out_chunks).decode("utf-8", errors="replace"),
        stderr=b"".join(err_chunks).decode("utf-8", errors="replace"),
    )
    if cancel is not None and cancel.cancelled:
        raise Cancelled()
    return result


def build_env(overrides: dict[str, str], *, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    env.update(overrides)
    return env
