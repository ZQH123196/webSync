"""远端列表解析。"""

from __future__ import annotations

import json

import pytest

from websync import remote
from websync.errors import WebSyncError


def test_parse_stream_lines() -> None:
    text = "\n".join(
        [
            json.dumps({"file_name": "20261029100147-novel.db", "size": 1713913856, "fid": "a"}),
            json.dumps({"file_name": "readme.md", "size": 12, "fid": "b"}),
        ]
    )

    files = remote.parse_listing(text)

    assert [item.name for item in files] == ["20261029100147-novel.db", "readme.md"]
    assert files[0].size == 1713913856


def test_parse_envelope_form() -> None:
    text = json.dumps(
        {
            "success": True,
            "data": {
                "list": [
                    {"file_name": "20261029100147-system.db", "size": 11108352, "fid": "c"},
                    {"file_name": "子目录", "dir": True},
                ]
            },
        },
        ensure_ascii=False,
    )

    entries = remote.parse_listing(text)

    assert [item.name for item in entries] == ["20261029100147-system.db", "子目录"]
    assert entries[1].is_dir is True


def test_parse_rejects_garbage() -> None:
    with pytest.raises(WebSyncError):
        remote.parse_listing("这不是 JSON")


def test_sorted_entries_puts_directories_first_then_newest() -> None:
    """目录在前，文件按修改时间倒序。"""
    entries = [
        remote.RemoteEntry("旧文件.txt", 1, mtime=1000),
        remote.RemoteEntry("新文件.txt", 5, mtime=3000),
        remote.RemoteEntry("中间文件.txt", 2, mtime=2000),
        remote.RemoteEntry("子目录", 0, is_dir=True),
        remote.RemoteEntry("另一个目录", 0, is_dir=True),
    ]

    picked = remote.sorted_entries(entries)

    assert [item.name for item in picked] == [
        "另一个目录",
        "子目录",
        "新文件.txt",
        "中间文件.txt",
        "旧文件.txt",
    ]
