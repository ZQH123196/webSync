"""运行期路径。

两类路径，来源不同：

- 配置跟着项目走：``<项目>/config/config.toml``。
- 外部二进制跟着项目走：``<项目>/bin/``，由 install-tools.sh 安装。
- 缓存不跟着项目走：暂存、日志、goquark 运行目录都在
  ``$XDG_CACHE_HOME/websync``（默认 ``~/.cache/websync``）。

缓存与暂存都是可丢弃的中间产物，随时能删，下次运行自动重建。
放项目里会让「复制一份」带上无意义的垃圾，也会把项目目录撑大。

项目根按顺序确定：环境变量 ``WEBSYNC_HOME`` → 本文件上溯三级（源码布局）
→ 当前工作目录。启动脚本会显式设置 ``WEBSYNC_HOME``，
所以从任何目录调起结果都一样。
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV = "WEBSYNC_HOME"


def project_root() -> Path:
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override).expanduser().resolve()

    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path.cwd().resolve()


def config_dir() -> Path:
    return project_root() / "config"


def default_config_path() -> Path:
    return config_dir() / "config.toml"


def bin_dir() -> Path:
    """项目自带的二进制目录，由 install-tools.sh 安装。"""
    return project_root() / "bin"


def cache_root() -> Path:
    """可丢弃的中间产物。默认 ``~/.cache/websync``，可用 ``XDG_CACHE_HOME`` 改。"""
    root = os.environ.get("XDG_CACHE_HOME") or "~/.cache"
    return Path(root).expanduser() / "websync"


def staging_dir() -> Path:
    return cache_root() / "staging"


def goquark_run_dir() -> Path:
    return cache_root() / "goquark"


def log_file() -> Path:
    return cache_root() / "logs" / "websync.log"
