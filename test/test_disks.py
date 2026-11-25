"""磁盘枚举与渲染。psutil 用替身，不碰真实挂载点。"""

from __future__ import annotations

from types import SimpleNamespace

import psutil
import pytest

from websync import disks


def _part(device: str, mountpoint: str, fstype: str) -> SimpleNamespace:
    return SimpleNamespace(device=device, mountpoint=mountpoint, fstype=fstype)


def test_collect_keeps_shortest_mountpoint_per_device(monkeypatch: pytest.MonkeyPatch) -> None:
    parts = [
        _part("/dev/sda1", "/home/zqh/Code/Project/trans_app", "ext4"),
        _part("/dev/sda1", "/", "ext4"),
        _part("/dev/sda1", "/snap", "ext4"),
    ]
    monkeypatch.setattr(psutil, "disk_partitions", lambda all=False: parts)
    monkeypatch.setattr(
        psutil,
        "disk_usage",
        lambda mountpoint: SimpleNamespace(total=1000 * 2**30, free=400 * 2**30),
    )

    rows = disks.collect()

    assert [row.mountpoint for row in rows] == ["/"]


def test_collect_collapses_aliased_network_mounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """WSL 把同一个 Windows 盘挂成 /mnt/c 与 /usr/lib/wsl/drivers。"""
    parts = [
        _part("C:\\", "/mnt/c", "9p"),
        _part("drivers", "/usr/lib/wsl/drivers", "9p"),
    ]
    monkeypatch.setattr(psutil, "disk_partitions", lambda all=False: parts)
    monkeypatch.setattr(
        psutil,
        "disk_usage",
        lambda mountpoint: SimpleNamespace(total=900 * 2**30, free=120 * 2**30),
    )

    rows = disks.collect()

    assert [row.mountpoint for row in rows] == ["/mnt/c"]


@pytest.mark.parametrize("fstype", ["tmpfs", "proc", "overlay", "fuse.snapfuse"])
def test_collect_skips_pseudo_filesystems(monkeypatch: pytest.MonkeyPatch, fstype: str) -> None:
    monkeypatch.setattr(
        psutil, "disk_partitions", lambda all=False: [_part("x", "/pseudo", fstype)]
    )
    monkeypatch.setattr(
        psutil, "disk_usage", lambda mountpoint: SimpleNamespace(total=2**30, free=2**30)
    )

    assert disks.collect() == []


def test_collect_skips_zero_size_and_unreadable_mounts(monkeypatch: pytest.MonkeyPatch) -> None:
    parts = [_part("/dev/zero", "/zero", "ext4"), _part("/dev/gone", "/gone", "ext4")]

    def usage(mountpoint: str) -> SimpleNamespace:
        if mountpoint == "/gone":
            raise PermissionError("不可读")
        return SimpleNamespace(total=0, free=0)

    monkeypatch.setattr(psutil, "disk_partitions", lambda all=False: parts)
    monkeypatch.setattr(psutil, "disk_usage", usage)

    assert disks.collect() == []


def test_render_marks_low_free_space() -> None:
    rows = [
        disks.DiskUsage("/", "/dev/sda1", "ext4", 1000 * 2**30, 500 * 2**30),
        disks.DiskUsage("/mnt/c", "C:\\", "9p", 900 * 2**30, 180 * 2**30),
        disks.DiskUsage("/data", "/dev/sdb1", "ext4", 100 * 2**30, 1 * 2**30),
    ]

    lines = disks.render(rows)

    assert lines[0][1].startswith("挂载点")
    styles = [style for style, _text in lines[1:]]
    assert styles == ["green", "yellow", "bold red"]


def test_render_handles_empty_list() -> None:
    assert disks.render([]) == [("dim", "没有可用的磁盘信息")]
