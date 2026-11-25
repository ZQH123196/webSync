"""业务流程：体检、推库、拉库。

TUI 与 CLI 都只调本模块。渲染层不碰子进程、不碰文件。
所有外部命令都经 :mod:`websync.transfer`，命令日志因此只有一处。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import export, remote, tools
from .config import Config
from .errors import (
    KIND_CONFIG,
    KIND_TOOL,
    KIND_TRANSFER,
    KIND_VERIFY,
    Cancelled,
    WebSyncError,
)
from .naming import SHA256_SUFFIX, human_size, make_prefix, snapshot_name
from .paths import cache_root, goquark_run_dir, staging_dir
from .tools import GOQUARK, KUAKE, SQLITE3, Tool
from .transfer import CancelToken, Progress, run_command

SCOPE_ALL = "all"
SCOPE_PUSH = "push"
SCOPE_PULL = "pull"

PROGRESS_CALLBACK = Callable[[Progress], None] | None
STEP_CALLBACK = Callable[[str], None] | None

VERIFY_ATTEMPTS = 8
REMOTE_TIMEOUT = 120.0
VERIFY_FIRST_DELAY = 1.0
VERIFY_MAX_DELAY = 16.0


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    scope: str = SCOPE_ALL


@dataclass(frozen=True)
class Toolset:
    kuake: Tool | None
    goquark: Tool | None
    sqlite3: Tool | None

    def require(self, name: str) -> Tool:
        tool = {KUAKE: self.kuake, GOQUARK: self.goquark, SQLITE3: self.sqlite3}[name]
        if tool is None:
            raise WebSyncError(
                KIND_TOOL,
                f"找不到 {name}",
                problems=[
                    "先安装该工具，或在配置的 [tools] 里写绝对路径",
                    "安装脚本见 docs/research/quark-relay/install-tools.sh",
                ],
            )
        return tool


@dataclass(frozen=True)
class PushOutcome:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class PullOutcome:
    name: str
    path: Path
    size: int
    sha256_verified: bool


def resolve_tools(config: Config) -> Toolset:
    return Toolset(
        kuake=tools.detect(KUAKE, config.kuake_bin),
        goquark=tools.detect(GOQUARK, config.goquark_bin),
        sqlite3=tools.detect(SQLITE3, config.sqlite3_bin),
    )


def remote_join(remote_dir: str, name: str) -> str:
    return f"{remote_dir.rstrip('/')}/{name}"


# ---------------------------------------------------------------- 凭证


def goquark_cookies(config: Config) -> dict[str, str]:
    """从 goquark 的登录态里取同一对 cookie。形状不对就当没有。"""
    path = config.goquark_config
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    session = payload.get("session")
    if not isinstance(session, dict):
        return {}
    cookies = session.get("cookies")
    if not isinstance(cookies, dict):
        return {}
    picked = {
        key: cookies[key]
        for key in ("__pus", "__puus")
        if isinstance(cookies.get(key), str) and cookies[key]
    }
    return picked if len(picked) == 2 else {}


def kuake_credential_source(config: Config) -> str:
    """凭证从哪来。doctor 显示用，也用于判断够不够。"""
    if os.environ.get("KUAKE_COOKIE"):
        return "环境变量 KUAKE_COOKIE"
    if os.environ.get("KUAKE_PUS") and os.environ.get("KUAKE_PUUS"):
        return "环境变量 KUAKE_PUS/KUAKE_PUUS"
    if goquark_cookies(config):
        return f"复用 {config.goquark_config}"
    return ""


def kuake_env(config: Config) -> dict[str, str]:
    """kuake 的凭证。

    已有环境变量优先，否则复用 goquark 的同一对 cookie。
    ``KUAKE_LOAD_DOTENV=0`` 关掉 kuake 自动加载当前目录 ``.env``：
    否则同一条命令在不同目录会用不同凭证。

    一份凭证都没有时直接报错。kuake 自己只回 ``INIT_ERROR``，
    那句话让人以为是环境变量没导出，实际是登录态没跟着项目走。
    """
    env = dict(os.environ)
    env["KUAKE_LOAD_DOTENV"] = "0"
    if env.get("KUAKE_COOKIE") or (env.get("KUAKE_PUS") and env.get("KUAKE_PUUS")):
        return env
    cookies = goquark_cookies(config)
    if not cookies:
        raise WebSyncError(
            KIND_CONFIG,
            "kuake 没有凭证",
            problems=[
                f"WebSync 复用 goquark 的登录态，缺 {config.goquark_config}",
                "登录态在用户主目录，不在项目里，换机器要单独拷这个文件",
                "或者设环境变量 KUAKE_PUS 与 KUAKE_PUUS",
            ],
        )
    env["KUAKE_PUS"] = cookies["__pus"]
    env["KUAKE_PUUS"] = cookies["__puus"]
    return env


def goquark_env(config: Config) -> dict[str, str]:
    """goquark 要可写配置目录，否则写日志失败且退出码仍是 0。

    复制出来的文件含 ``__pus``/``__puus``，等同密码，因此收紧到 0600，
    目录 0700。
    """
    source = config.goquark_config
    if not source.is_file():
        raise WebSyncError(
            KIND_TOOL,
            "goquark 未登录",
            problems=[f"缺少 {source}，先跑一次 goquark 登录"],
        )
    run_dir = goquark_run_dir()
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        run_dir.chmod(0o700)
        target = run_dir / "config.json"
        shutil.copyfile(source, target)
        target.chmod(0o600)
    except OSError as exc:
        raise WebSyncError(
            KIND_TOOL,
            f"goquark 运行目录不可用：{run_dir}",
            problems=[str(exc), "可用 XDG_CACHE_HOME 指到可写位置"],
        ) from exc
    return {
        **os.environ,
        "GOQUARK_CONFIG": str(target),
        "GOQUARK_DOWNLOAD_SERVICE": "off",
    }


# ---------------------------------------------------------------- 工具函数


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_remote(
    config: Config,
    toolset: Toolset,
    remote_dir: str | None = None,
    cancel: CancelToken | None = None,
) -> list[remote.RemoteEntry]:
    """读远端一个目录。目录在前，文件按时间倒序。"""
    kuake = toolset.require(KUAKE)
    target = remote_dir or config.remote_dir
    entries = remote.list_dir(kuake, target, env=kuake_env(config), cancel=cancel)
    return remote.sorted_entries(entries)


def create_remote_dir(
    config: Config,
    toolset: Toolset,
    name: str,
    remote_dir: str,
    cancel: CancelToken | None = None,
) -> None:
    """在远端建一个目录。同名已存在时 kuake 也返回成功，不额外处理。"""
    kuake = toolset.require(KUAKE)
    result = run_command(
        [str(kuake.path), "create", name, remote_dir],
        env=kuake_env(config),
        timeout=REMOTE_TIMEOUT,
        cancel=cancel,
    )
    if result.returncode != 0:
        raise WebSyncError(
            KIND_TRANSFER, f"建目录失败：{remote_join(remote_dir, name)}", problems=[result.tail]
        )
    payload = parse_kuake_envelope(result.stdout, f"建目录 {name}")
    if payload.get("success") is not True:
        raise WebSyncError(
            KIND_TRANSFER,
            f"建目录被拒绝：{remote_join(remote_dir, name)}",
            problems=[str(payload.get("message", ""))],
        )


def parse_kuake_envelope(stdout: str, action: str) -> dict:
    """kuake 的 stdout 是 JSON 信封。解析不了说明这条命令没走到正常路径。"""
    text = stdout.strip()
    if not text:
        raise WebSyncError(KIND_TRANSFER, f"{action}没有返回任何内容", problems=["看命令日志"])
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WebSyncError(
            KIND_TRANSFER,
            f"{action}的输出不是 JSON",
            problems=[str(exc), text[-400:]],
        ) from exc
    if not isinstance(payload, dict):
        raise WebSyncError(KIND_TRANSFER, f"{action}的返回不是对象", problems=[text[-400:]])
    return payload


def require_upload_ok(result_stdout: str, expected: int, name: str) -> None:
    """判定一次上传真的落地了。

    kuake 遇同名会返回 ``success: true`` 与 ``SKIPPED``，退出码也是 0。
    只认退出码会把「静默跳过」当成成功，这里必须解析信封。
    """
    payload = parse_kuake_envelope(result_stdout, f"上传 {name}")
    code = str(payload.get("code", ""))
    if payload.get("success") is not True:
        raise WebSyncError(
            KIND_TRANSFER,
            f"上传失败：{name}",
            problems=[str(payload.get("message") or code or "未知原因")],
        )
    if code != "OK":
        hint = "kuake 遇同名默认跳过，且不比对大小" if code == "SKIPPED" else "换一个时间戳前缀重来"
        raise WebSyncError(
            KIND_VERIFY,
            f"上传未真正写入：{name}（{code}）",
            problems=[str(payload.get("message") or ""), hint],
        )
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("size"), int):
        raise WebSyncError(
            KIND_TRANSFER,
            f"上传返回缺 size：{name}",
            problems=[str(payload)[:400]],
        )
    if data["size"] != expected:
        raise WebSyncError(
            KIND_VERIFY,
            f"上传字节数不一致：{name}",
            problems=[f"本地 {expected} B，服务端报告 {data['size']} B"],
        )


def _wait_for_remote(
    config: Config,
    toolset: Toolset,
    name: str,
    expected: int,
    remote_dir: str,
    *,
    attempts: int = VERIFY_ATTEMPTS,
    first_delay: float = VERIFY_FIRST_DELAY,
    on_step: STEP_CALLBACK = None,
    cancel: CancelToken | None = None,
) -> int:
    """上传后立刻 list 可能看不到新文件，必须退避重试。

    1.6 GB 上传后索引可能滞后几十秒，所以退避到一分钟量级，
    并把 list 自身的瞬时失败也算作「还没看到」。
    """
    kuake = toolset.require(KUAKE)
    last = -1
    delay = first_delay
    problems: list[str] = []
    for attempt in range(1, attempts + 1):
        if cancel is not None and cancel.cancelled:
            raise Cancelled()
        try:
            files = remote.list_dir(
                kuake, remote_dir, env=kuake_env(config), cancel=cancel
            )
        except Cancelled:
            raise
        except WebSyncError as exc:
            problems = [exc.message, *exc.problems]
        else:
            found = remote.find(files, name)
            if found is not None:
                last = found.size
                if found.size == expected:
                    return found.size
            if on_step:
                on_step(f"远端还没看到 {name}（第 {attempt} 次），稍后重试")
        if attempt < attempts:
            time.sleep(delay)
            delay = min(delay * 2, VERIFY_MAX_DELAY)
    raise WebSyncError(
        KIND_VERIFY,
        f"远端字节数与本地不一致：{name}",
        problems=[f"本地 {expected} B，远端 {last if last >= 0 else '缺文件'} B", *problems],
    )


# ---------------------------------------------------------------- 上传


def _stage(source: Path, token: str) -> Path:
    """把要传的东西准备到暂存目录。

    SQLite 库走 ``.backup`` 拿一致快照；其他文件原样复制。
    暂存名与远端名解耦：文件名里可能有空格或引号，
    而 sqlite3 的 ``.backup`` 是命令行参数，夹带这些字符会直接失败。
    """
    staged = staging_dir() / f"{token}.upload"
    try:
        staged.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WebSyncError(
            KIND_TRANSFER,
            f"暂存目录不可写：{staged.parent}",
            problems=[str(exc), "可用 XDG_CACHE_HOME 指到可写位置"],
        ) from exc
    return staged


def _upload_staged(
    config: Config,
    toolset: Toolset,
    staged: Path,
    remote_dir: str,
    name: str,
    *,
    on_progress: PROGRESS_CALLBACK = None,
    on_step: STEP_CALLBACK = None,
    cancel: CancelToken | None = None,
    verify_attempts: int = VERIFY_ATTEMPTS,
    verify_delay: float = VERIFY_FIRST_DELAY,
) -> PushOutcome:
    """把一个准备好的文件传到远端：先传 sha256 旁文件，再传主文件，最后核对字节数。"""
    kuake = toolset.require(KUAKE)
    sidecar_name = f"{name}{SHA256_SUFFIX}"
    remote_path = remote_join(remote_dir, name)
    sidecar_remote = remote_join(remote_dir, sidecar_name)

    if on_step:
        on_step(f"检查远端是否已有 {name}")
    existing = remote.find(
        remote.list_dir(kuake, remote_dir, env=kuake_env(config), cancel=cancel),
        name,
    )
    if existing is not None:
        raise WebSyncError(
            KIND_VERIFY,
            f"远端已有同名文件：{name}",
            problems=["kuake 遇同名会静默跳过且不比对大小，换一个时间戳前缀重来"],
        )

    size = staged.stat().st_size
    digest = sha256_of(staged)
    sidecar = staged.with_name(staged.name + SHA256_SUFFIX)
    try:
        # 先传校验文件再传主文件：远端不会出现「有主文件、没有校验文件」的状态。
        # 反过来失败一次，之后拉库就只能核对字节数。
        if on_step:
            on_step("上传校验文件")
        sidecar.write_text(f"{digest}  {name}\n", encoding="utf-8")
        sidecar_size = sidecar.stat().st_size
        sidecar_result = run_command(
            [str(kuake.path), "upload", str(sidecar), sidecar_remote],
            env=kuake_env(config),
            timeout=None,
            cancel=cancel,
        )
        if sidecar_result.returncode != 0:
            raise WebSyncError(
                KIND_TRANSFER,
                f"上传校验文件失败：{sidecar_name}",
                problems=[sidecar_result.tail],
            )
        require_upload_ok(sidecar_result.stdout, sidecar_size, sidecar_name)
        _wait_for_remote(
            config,
            toolset,
            sidecar_name,
            sidecar_size,
            remote_dir,
            attempts=verify_attempts,
            first_delay=verify_delay,
            on_step=on_step,
            cancel=cancel,
        )

        if on_step:
            on_step(f"上传 {name}（{human_size(size)}）")
        result = run_command(
            [str(kuake.path), "upload", str(staged), remote_path],
            env=kuake_env(config),
            timeout=None,
            on_progress=on_progress,
            cancel=cancel,
        )
        if result.returncode != 0:
            raise WebSyncError(KIND_TRANSFER, f"上传失败：{name}", problems=[result.tail])
        require_upload_ok(result.stdout, size, name)

        if on_step:
            on_step("核对远端字节数")
        _wait_for_remote(
            config,
            toolset,
            name,
            size,
            remote_dir,
            attempts=verify_attempts,
            first_delay=verify_delay,
            on_step=on_step,
            cancel=cancel,
        )
    finally:
        sidecar.unlink(missing_ok=True)

    return PushOutcome(name=name, size=size, sha256=digest)


def push(
    config: Config,
    toolset: Toolset,
    source: Path,
    remote_dir: str | None = None,
    *,
    prefix: str | None = None,
    on_progress: PROGRESS_CALLBACK = None,
    on_step: STEP_CALLBACK = None,
    cancel: CancelToken | None = None,
    verify_attempts: int = VERIFY_ATTEMPTS,
    verify_delay: float = VERIFY_FIRST_DELAY,
) -> PushOutcome:
    """上传一个文件。SQLite 库先做一致快照，其他文件原样上传。"""
    target_dir = remote_dir or config.remote_dir
    source = source.expanduser()
    if not source.is_file():
        raise WebSyncError(KIND_TRANSFER, f"本地文件不存在：{source}")

    stamp = prefix or make_prefix()
    name = snapshot_name(stamp, source.name)
    staged = _stage(source, uuid.uuid4().hex)
    try:
        if on_step:
            on_step(f"准备 {source.name}")
        if export.is_sqlite(source):
            if on_step:
                on_step(f"{source.name} 是 SQLite 库：导出快照后上传，源库可继续读写")
            sqlite3 = toolset.require(SQLITE3)
            export.export_snapshot(sqlite3.path, source, staged, cancel=cancel)
            if on_step:
                on_step(f"快照通过完整性检查：{human_size(staged.stat().st_size)}")
        else:
            if on_step:
                on_step(f"{source.name} 不是 SQLite：原样上传，不做快照")
            shutil.copyfile(source, staged)
        return _upload_staged(
            config,
            toolset,
            staged,
            target_dir,
            name,
            on_progress=on_progress,
            on_step=on_step,
            cancel=cancel,
            verify_attempts=verify_attempts,
            verify_delay=verify_delay,
        )
    finally:
        # 暂存文件不复用：失败重做只要几秒，留着会白占 1.6 GB。
        staged.unlink(missing_ok=True)


def push_tree(
    config: Config,
    toolset: Toolset,
    source: Path,
    remote_dir: str | None = None,
    *,
    prefix: str | None = None,
    on_progress: PROGRESS_CALLBACK = None,
    on_step: STEP_CALLBACK = None,
    cancel: CancelToken | None = None,
    verify_attempts: int = VERIFY_ATTEMPTS,
    verify_delay: float = VERIFY_FIRST_DELAY,
) -> list[PushOutcome]:
    """上传整个目录。

    远端建一个 ``<前缀>-<目录名>`` 的新目录，内部结构和文件名照搬，
    不再给每个文件加前缀：新目录里不会有同名冲突。
    """
    target_dir = remote_dir or config.remote_dir
    source = source.expanduser()
    if not source.is_dir():
        raise WebSyncError(KIND_TRANSFER, f"本地目录不存在：{source}")

    stamp = prefix or make_prefix()
    root = snapshot_name(stamp, source.name)
    if on_step:
        on_step(f"建远端目录 {remote_join(target_dir, root)}")
    create_remote_dir(config, toolset, root, target_dir, cancel=cancel)

    outcomes: list[PushOutcome] = []
    for path in sorted(source.rglob("*")):
        if cancel is not None and cancel.cancelled:
            raise Cancelled()
        relative = path.relative_to(source)
        parent_rel = "/".join(relative.parts[:-1])
        sub_dir = (
            remote_join(remote_join(target_dir, root), parent_rel)
            if parent_rel
            else remote_join(target_dir, root)
        )
        if path.is_dir():
            create_remote_dir(config, toolset, path.name, sub_dir, cancel=cancel)
            continue
        if not path.is_file():
            continue

        staged = _stage(path, uuid.uuid4().hex)
        try:
            if on_step:
                on_step(f"准备 {relative}")
            if export.is_sqlite(path):
                if on_step:
                    on_step(f"{relative} 是 SQLite 库：导出快照后上传，源库可继续读写")
                sqlite3 = toolset.require(SQLITE3)
                export.export_snapshot(sqlite3.path, path, staged, cancel=cancel)
                if on_step:
                    on_step(f"快照通过完整性检查：{human_size(staged.stat().st_size)}")
            else:
                shutil.copyfile(path, staged)
            outcomes.append(
                _upload_staged(
                    config,
                    toolset,
                    staged,
                    sub_dir,
                    path.name,
                    on_progress=on_progress,
                    on_step=on_step,
                    cancel=cancel,
                    verify_attempts=verify_attempts,
                    verify_delay=verify_delay,
                )
            )
        finally:
            staged.unlink(missing_ok=True)
    return outcomes


# ---------------------------------------------------------------- 下载


def pull(
    config: Config,
    toolset: Toolset,
    target: remote.RemoteEntry,
    local_dir: Path,
    remote_dir: str | None = None,
    *,
    on_progress: PROGRESS_CALLBACK = None,
    on_step: STEP_CALLBACK = None,
    cancel: CancelToken | None = None,
) -> PullOutcome:
    """把一个远端文件下载到 ``local_dir``。"""
    goquark = toolset.require(GOQUARK)
    kuake = toolset.require(KUAKE)
    source_dir = remote_dir or config.remote_dir

    local_dir = local_dir.expanduser()
    try:
        local_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WebSyncError(
            KIND_TRANSFER, f"落地目录不可写：{local_dir}", problems=[str(exc)]
        ) from exc

    final = local_dir / target.name
    part = local_dir / f"{target.name}.part"
    sidecar_part = local_dir / f"{target.name}{SHA256_SUFFIX}.part"
    part.unlink(missing_ok=True)
    sidecar_part.unlink(missing_ok=True)

    verified = False
    try:
        remote_path = remote_join(source_dir, target.name)
        if on_step:
            on_step(f"下载 {target.name}（{human_size(target.size)}）")
        result = run_command(
            [str(goquark.path), "download", remote_path, str(part)],
            env=goquark_env(config),
            timeout=None,
            on_progress=on_progress,
            cancel=cancel,
        )
        # goquark 失败时退出码仍是 0，只能认 stdout 里的 OK 行。
        if "OK path=" not in result.stdout:
            raise WebSyncError(KIND_TRANSFER, f"下载失败：{target.name}", problems=[result.tail])
        if not part.is_file() or part.stat().st_size == 0:
            raise WebSyncError(KIND_TRANSFER, f"下载产物为空：{part}")

        size = part.stat().st_size
        if target.size > 0 and size != target.size:
            raise WebSyncError(
                KIND_VERIFY,
                f"下载字节数与远端不一致：{target.name}",
                problems=[f"远端 {target.size} B，本地 {size} B"],
            )

        sidecar_name = f"{target.name}{SHA256_SUFFIX}"
        files = remote.list_dir(kuake, source_dir, env=kuake_env(config), cancel=cancel)
        if remote.find(files, sidecar_name) is not None:
            if on_step:
                on_step("校验 sha256")
            sidecar_result = run_command(
                [
                    str(goquark.path),
                    "download",
                    remote_join(source_dir, sidecar_name),
                    str(sidecar_part),
                ],
                env=goquark_env(config),
                timeout=None,
                cancel=cancel,
            )
            if "OK path=" not in sidecar_result.stdout or not sidecar_part.is_file():
                raise WebSyncError(
                    KIND_TRANSFER,
                    f"下载校验文件失败：{sidecar_name}",
                    problems=[sidecar_result.tail],
                )
            expected = sidecar_part.read_text(encoding="utf-8", errors="replace").split()
            actual = sha256_of(part)
            if expected and expected[0] != actual:
                raise WebSyncError(
                    KIND_VERIFY,
                    f"sha256 不匹配：{target.name}",
                    problems=[f"期望 {expected[0]}", f"实际 {actual}"],
                )
            verified = True

        part.replace(final)
        return PullOutcome(name=target.name, path=final, size=size, sha256_verified=verified)
    finally:
        part.unlink(missing_ok=True)
        sidecar_part.unlink(missing_ok=True)


def pull_tree(
    config: Config,
    toolset: Toolset,
    target: remote.RemoteEntry,
    local_dir: Path,
    remote_dir: str | None = None,
    *,
    on_progress: PROGRESS_CALLBACK = None,
    on_step: STEP_CALLBACK = None,
    cancel: CancelToken | None = None,
) -> list[PullOutcome]:
    """下载整个远端目录。逐个文件走 goquark，本地结构照搬。"""
    source_dir = remote_join(remote_dir or config.remote_dir, target.name)
    destination = local_dir.expanduser() / target.name
    if on_step:
        on_step(f"读远端目录 {source_dir}")
    entries = list_remote(config, toolset, source_dir, cancel=cancel)
    if not entries:
        raise WebSyncError(KIND_TRANSFER, f"远端目录是空的：{source_dir}")

    outcomes: list[PullOutcome] = []
    for entry in entries:
        if cancel is not None and cancel.cancelled:
            raise Cancelled()
        if entry.is_dir:
            outcomes.extend(
                pull_tree(
                    config,
                    toolset,
                    entry,
                    destination,
                    source_dir,
                    on_progress=on_progress,
                    on_step=on_step,
                    cancel=cancel,
                )
            )
            continue
        outcomes.append(
            pull(
                config,
                toolset,
                remote.RemoteEntry(
                    name=entry.name,
                    size=entry.size,
                    mtime=entry.mtime,
                ),
                destination,
                source_dir,
                on_progress=on_progress,
                on_step=on_step,
                cancel=cancel,
            )
        )
    return outcomes


# ---------------------------------------------------------------- 体检


def _writable(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".websync-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, f"{path}：{exc}"
    return True, str(path)


def staging_free() -> int:
    """暂存盘的可用字节数。

    暂存目录可能还没建，那就按最近的已存在上级目录算。不在这里建目录：
    用户可能只是打开确认框看一眼就取消。
    """
    path = staging_dir()
    while not path.exists() and path.parent != path:
        path = path.parent
    return shutil.disk_usage(path).free


def doctor(config: Config, toolset: Toolset | None = None) -> list[Check]:
    """一次性收集全部环境问题。不做短路，一次报完。"""
    toolset = toolset or resolve_tools(config)
    checks: list[Check] = [Check("配置文件", True, str(config.path))]

    for name, tool, scope in (
        (KUAKE, toolset.kuake, SCOPE_ALL),
        (GOQUARK, toolset.goquark, SCOPE_PULL),
        (SQLITE3, toolset.sqlite3, SCOPE_PUSH),
    ):
        if tool is None:
            checks.append(Check(name, False, "未找到可执行文件", scope))
        else:
            version = tools.probe_version(tool)
            checks.append(
                Check(name, True, f"{tool.path}" + (f"（{version}）" if version else ""), scope)
            )

    source = kuake_credential_source(config)
    checks.append(
        Check(
            "kuake 凭证",
            bool(source),
            source or f"{config.goquark_config} 里缺 __pus/__puus",
            SCOPE_ALL,
        )
    )

    if toolset.kuake is None:
        checks.append(Check("远端目录", False, "缺 kuake，无法列远端", SCOPE_ALL))
    else:
        try:
            files = remote.list_dir(toolset.kuake, config.remote_dir, env=kuake_env(config))
        except WebSyncError as exc:
            checks.append(Check("远端目录", False, exc.message, SCOPE_ALL))
        else:
            entries = remote.sorted_entries(files)
            checks.append(
                Check(
                    "远端目录",
                    True,
                    f"{config.remote_dir}，{len(entries)} 条",
                    SCOPE_ALL,
                )
            )

    if toolset.goquark is not None:
        ok = bool(goquark_cookies(config))
        checks.append(
            Check(
                "goquark 登录",
                ok,
                "会话 cookie 齐全" if ok else f"{config.goquark_config} 缺 __pus/__puus",
                SCOPE_PULL,
            )
        )

    if config.db_dir is None:
        checks.append(
            Check("上传目录", False, "未配置 [local].db_dir（只有推库的机子需要）", SCOPE_PUSH)
        )
        need = 0
    else:
        files = config.upload_files()
        names = "、".join(entry.name for entry in files[:4]) or "空"
        checks.append(
            Check(
                "上传目录",
                bool(files),
                f"{config.db_dir}，{len(files)} 个文件：{names}",
                SCOPE_PUSH,
            )
        )
        need = sum(entry.stat().st_size for entry in files)

    staging = staging_dir()
    writable, detail = _writable(staging)
    if writable:
        free = shutil.disk_usage(staging).free
        checks.append(
            Check(
                "暂存目录",
                free > need,
                f"{detail}，可用 {human_size(free)}，导出需要 {human_size(need)}",
                SCOPE_PUSH,
            )
        )
    else:
        checks.append(Check("暂存目录", False, detail, SCOPE_PUSH))

    download = config.start_dir()
    writable, detail = _writable(download)
    checks.append(Check("下载目录", writable, detail, SCOPE_PULL))
    if writable:
        checks.append(
            Check(
                "下载目录余量",
                True,
                f"可用 {human_size(shutil.disk_usage(download).free)}",
                SCOPE_PULL,
            )
        )

    writable, detail = _writable(cache_root())
    checks.append(Check("缓存目录", writable, detail, SCOPE_ALL))
    return checks
