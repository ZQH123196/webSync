"""磁盘余量。

取挂载点用 psutil，跨平台。两轮去重，避免把同一块盘显示很多遍：

1. 同一 device 挂载在多处（bind mount、snap、docker）只留最短挂载点。
2. 同 fstype、同容量、同余量的不同 device 只留最短挂载点。
   WSL 会把 Windows 的 C: 同时挂成 ``/mnt/c`` 与 ``/usr/lib/wsl/drivers``。

伪文件系统一律排除：它们要么容量为 0，要么与真实磁盘无关。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .naming import human_size

PSEUDO_FSTYPES = frozenset(
    {
        "proc",
        "sysfs",
        "devtmpfs",
        "devpts",
        "tmpfs",
        "cgroup",
        "cgroup2",
        "mqueue",
        "hugetlbfs",
        "debugfs",
        "tracefs",
        "configfs",
        "fusectl",
        "binfmt_misc",
        "pstore",
        "securityfs",
        "autofs",
        "nsfs",
        "rootfs",
        "overlay",
        "squashfs",
        "ramfs",
        "selinuxfs",
        "efivarfs",
        "bpf",
        "rpc_pipefs",
        "fuse.snapfuse",
        "fuse.lxcfs",
        "fuse.gvfsd-fuse",
    }
)

REFRESH_SECONDS = 5.0

LOW_FREE = 10.0
TIGHT_FREE = 30.0


@dataclass(frozen=True)
class DiskUsage:
    mountpoint: str
    device: str
    fstype: str
    total: int
    free: int

    @property
    def free_percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.free / self.total * 100.0

    @property
    def style(self) -> str:
        if self.free_percent < LOW_FREE:
            return "bold red"
        if self.free_percent < TIGHT_FREE:
            return "yellow"
        return "green"


def collect() -> list[DiskUsage]:
    """枚举真实磁盘。取不到用量的挂载点直接跳过。"""
    import psutil

    rows: list[DiskUsage] = []
    for part in psutil.disk_partitions(all=True):
        if part.fstype.lower() in PSEUDO_FSTYPES:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        if usage.total <= 0:
            continue
        rows.append(
            DiskUsage(
                mountpoint=part.mountpoint,
                device=part.device,
                fstype=part.fstype,
                total=usage.total,
                free=usage.free,
            )
        )

    # 第一轮：同一 device 只留最短挂载点
    by_device: dict[str, DiskUsage] = {}
    for row in sorted(rows, key=lambda item: (len(item.mountpoint), item.mountpoint)):
        by_device.setdefault(row.device, row)

    # 第二轮：同 fstype + 容量 + 余量的别名挂载也只留最短的
    by_shape: dict[tuple[str, int, int], DiskUsage] = {}
    for row in sorted(by_device.values(), key=lambda item: (len(item.mountpoint), item.mountpoint)):
        by_shape.setdefault((row.fstype, row.total, row.free), row)

    return sorted(by_shape.values(), key=lambda item: item.mountpoint)


POINT_MIN_WIDTH = 8
SIZE_WIDTH = 12
PERCENT_WIDTH = 6


def _width(text: str) -> int:
    """终端显示宽度。CJK 占两格，``len`` 数的是字数，两者不同。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _pad(text: str, width: int, align: str = "<") -> str:
    """按显示宽度补齐。用 ``str.ljust`` 会把 CJK 表头挤歪。"""
    fill = " " * max(0, width - _width(text))
    return fill + text if align == ">" else text + fill


def render(disks: list[DiskUsage]) -> list[tuple[str, str]]:
    """返回 ``(样式, 文本)`` 列表。文本对齐，样式由调用方套 markdown 标记。

    对齐按终端显示宽度算，不按字数：CJK 字符占两格。
    """
    if not disks:
        return [("dim", "没有可用的磁盘信息")]

    point_width = max(POINT_MIN_WIDTH, max(_width(item.mountpoint) for item in disks))
    header = "  ".join(
        (
            _pad("挂载点", point_width),
            _pad("可用", SIZE_WIDTH, ">"),
            _pad("总量", SIZE_WIDTH, ">"),
            _pad("可用率", PERCENT_WIDTH, ">"),
        )
    )
    lines = [("bold", header)]
    for item in disks:
        text = "  ".join(
            (
                _pad(item.mountpoint, point_width),
                _pad(human_size(item.free), SIZE_WIDTH, ">"),
                _pad(human_size(item.total), SIZE_WIDTH, ">"),
                _pad(f"{item.free_percent:.0f}%", PERCENT_WIDTH, ">"),
            )
        )
        lines.append((item.style, text))
    return lines
