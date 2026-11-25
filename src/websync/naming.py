"""文件名、时间与尺寸的口径。

时间一律算北京时间。不依赖 ``Asia/Shanghai`` 的 tzdata：极简容器里
没有时区库时 ZoneInfo 会在导入期直接崩，而中国没有夏令时，
固定 +08:00 与前者等价。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
PREFIX_FORMAT = "%Y%m%d%H%M%S"
PREFIX_LEN = 14

PREFIX_RE = re.compile(r"^(?P<prefix>\d{14})-(?P<stem>.+)$")
SHA256_SUFFIX = ".sha256"


def now_beijing() -> datetime:
    return datetime.now(BEIJING)


def make_prefix(moment: datetime | None = None) -> str:
    """生成 14 位时间戳前缀。"""
    return (moment or now_beijing()).astimezone(BEIJING).strftime(PREFIX_FORMAT)


def snapshot_name(prefix: str, filename: str) -> str:
    """远端文件名 = 时间戳前缀 + 原文件名。原文件名不动。"""
    if len(prefix) != PREFIX_LEN or not prefix.isdigit():
        raise ValueError(f"前缀必须是 14 位数字：{prefix}")
    if not filename or "/" in filename or "\\" in filename:
        raise ValueError(f"文件名必须是不带路径的裸名：{filename}")
    return f"{prefix}-{filename}"


def parse_prefix_datetime(prefix: str) -> datetime | None:
    """把前缀还原成北京时间。日期非法则返回 ``None``，不抛错。"""
    try:
        return datetime.strptime(prefix, PREFIX_FORMAT).replace(tzinfo=BEIJING)
    except ValueError:
        return None


def parse_snapshot(name: str) -> tuple[str, str] | None:
    """拆出 ``(前缀, 文件名)``。

    只要满足「14 位合法日期 + 连字符 + 任意文件名」就算快照。
    校验旁文件 ``.sha256`` 不算。
    """
    if name.endswith(SHA256_SUFFIX):
        return None
    matched = PREFIX_RE.match(name)
    if not matched:
        return None
    prefix = matched.group("prefix")
    if parse_prefix_datetime(prefix) is None:
        return None
    return prefix, matched.group("stem")


def human_size(size: int) -> str:
    current = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if current < 1024 or unit == "TiB":
            return f"{current:.0f} {unit}" if unit == "B" else f"{current:.2f} {unit}"
        current /= 1024
    return f"{current:.2f} TiB"
