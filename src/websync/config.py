"""配置加载与校验。

本模块是配置读取的唯一入口：其余模块只接受 :class:`Config`。
配置项不合法时一次性报完所有问题，再抛 :class:`WebSyncError`。
禁止任何模块在旁边自建 ``tomllib`` 读取路径。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import KIND_CONFIG, WebSyncError
from .paths import default_config_path, project_root

CONFIG_ENV = "WEBSYNC_CONFIG"

NOISE_SUFFIXES = ("-wal", "-shm", "-journal", ".part", ".sha256")

DEFAULT_SQLITE3 = "sqlite3"
DEFAULT_GOQUARK_CONFIG = "config/goquark.json"

CONFIG_TEMPLATE = """\
# WebSync 配置。跟着项目走，复制整个目录即可使用。
# 位置：<项目>/config/config.toml（可用环境变量 WEBSYNC_CONFIG 覆盖）
#
# 相对路径一律相对项目根目录解析，不随调用时的工作目录变化。

[remote]
# 夸克网盘上的中转目录。
dir = "/我的项目/db"

[local]
# 上传目录。这里放什么就能传什么，不用登记。只有推库的机子需要。
# 本仓库内用相对路径，换机器不用改。
db_dir = "../../server/db/database"
# 下载目录。下载的文件落在这里，本地窗口一打开也在这里。
download_dir = "~/download"

[tools]
# 留空表示按名字在 PATH 与 ~/.local/bin 里自动探测。
kuake_bin = ""
goquark_bin = ""
sqlite3_bin = "sqlite3"
# goquark 的登录态。kuake 缺凭证时复用这里面同一对 cookie。
# 放项目里，整包复制就能用。含会话 cookie，等同密码，不入 git。
goquark_config = "config/goquark.json"
"""


@dataclass(frozen=True)
class Config:
    path: Path
    remote_dir: str
    db_dir: Path | None
    download_dir: Path
    kuake_bin: str
    goquark_bin: str
    sqlite3_bin: str
    goquark_config: Path

    def start_dir(self) -> Path:
        """下载目录。本地窗口一打开就落在这里。"""
        return self.download_dir.expanduser()

    def upload_files(self) -> list[Path]:
        """上传目录里能传的文件，按修改时间倒序。"""
        if self.db_dir is None or not self.db_dir.is_dir():
            return []
        picked = [entry for entry in self.db_dir.iterdir() if entry.is_file() and is_visible(entry.name)]
        picked.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
        return picked


def is_visible(name: str) -> bool:
    """临时文件与下载残留不算用户要看的东西。

    ``-wal`` / ``-shm`` / ``-journal`` 是 SQLite 的运行时文件，
    ``.part`` 是没下完的，``.sha256`` 是本工具自己的校验旁文件。
    """
    return not name.endswith(NOISE_SUFFIXES) and not name.startswith(".")


def config_path() -> Path:
    """配置文件的唯一解析出口。"""
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    return default_config_path()


def _absolute(raw: str) -> Path:
    """相对路径按**项目根目录**解析成绝对路径。

    两个理由：导出时子进程的 cwd 会切到暂存目录，不钉死就会
    「存在性检查」与「实际执行」用的不是同一个位置；配置跟着项目走，
    相对项目根才可复制，相对 cwd 则换个目录启动就失效。
    """
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return (project_root() / path).resolve()


def load(path: Path | None = None) -> Config:
    """读取并校验配置。缺失或非法一律抛错，不做默认值兜底。"""
    target = path or config_path()
    if not target.is_file():
        raise WebSyncError(
            KIND_CONFIG,
            f"配置不存在：{target}",
            problems=["先跑 `websync init` 生成模板", f"或手工创建 {target}"],
        )
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise WebSyncError(KIND_CONFIG, f"配置不是合法 TOML：{target}", problems=[str(exc)]) from exc

    problems: list[str] = []
    remote = raw.get("remote", {})
    local = raw.get("local", {})
    tools = raw.get("tools", {})

    if "databases" in raw:
        problems.append("[databases] 已取消：上传目录里的文件都会列出，不用登记")

    if not isinstance(remote, dict):
        problems.append("[remote] 必须是表")
        remote = {}
    if not isinstance(local, dict):
        problems.append("[local] 必须是表")
        local = {}
    if not isinstance(tools, dict):
        problems.append("[tools] 必须是表")
        tools = {}

    remote_dir = str(remote.get("dir", "")).strip()
    if not remote_dir:
        problems.append("[remote].dir 不能为空")
    elif not remote_dir.startswith("/"):
        problems.append(f"[remote].dir 必须以 / 开头：{remote_dir}")

    db_dir_raw = str(local.get("db_dir", "")).strip()
    db_dir = _absolute(db_dir_raw) if db_dir_raw else None

    if "inbox_dir" in local:
        problems.append("[local].inbox_dir 已改名：用 download_dir")
    download_raw = str(local.get("download_dir", "")).strip()
    download_dir = Path(".") if not download_raw else _absolute(download_raw)
    if not download_raw:
        problems.append("[local].download_dir 不能为空")

    sqlite3_bin = str(tools.get("sqlite3_bin", "")).strip() or DEFAULT_SQLITE3
    kuake_bin = str(tools.get("kuake_bin", "")).strip()
    goquark_bin = str(tools.get("goquark_bin", "")).strip()
    goquark_config = _absolute(
        str(tools.get("goquark_config", "")).strip() or DEFAULT_GOQUARK_CONFIG
    )

    if problems:
        raise WebSyncError(KIND_CONFIG, f"配置有问题：{target}", problems=problems)

    return Config(
        path=target,
        remote_dir=remote_dir,
        db_dir=db_dir,
        download_dir=download_dir,
        kuake_bin=kuake_bin,
        goquark_bin=goquark_bin,
        sqlite3_bin=sqlite3_bin,
        goquark_config=goquark_config,
    )


def write_template(path: Path | None = None, *, force: bool = False) -> Path:
    """写出配置模板。目标已存在且未 force 时拒绝覆盖。"""
    target = path or config_path()
    if target.exists() and not force:
        raise WebSyncError(
            KIND_CONFIG,
            f"配置已存在：{target}",
            problems=["需要覆盖时加 --force"],
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return target
