"""文件名与时间口径。"""

from __future__ import annotations

from datetime import datetime

import pytest

from websync import naming


def test_prefix_uses_beijing_time_regardless_of_local_zone() -> None:
    moment = datetime.fromisoformat("2026-10-29T02:30:00+00:00")

    assert naming.make_prefix(moment) == "20261029103000"


def test_snapshot_name_keeps_original_filename() -> None:
    assert naming.snapshot_name("20261029100147", "novel.db") == "20261029100147-novel.db"
    assert naming.snapshot_name("20261029100147", "我的 报告.xlsx") == "20261029100147-我的 报告.xlsx"
    with pytest.raises(ValueError):
        naming.snapshot_name("2026-10-29", "novel.db")
    with pytest.raises(ValueError):
        naming.snapshot_name("20261029100147", "a/b.db")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("20261029100147-novel.db", ("20261029100147", "novel.db")),
        ("20261029100147-report.xlsx", ("20261029100147", "report.xlsx")),
        ("20261029100147-novel.db.sha256", None),
        ("novel.db", None),
        ("20261029100147-", None),
        ("20261399999999-novel.db", None),
        ("20260230000000-novel.db", None),
    ],
)
def test_parse_snapshot(name: str, expected: tuple[str, str] | None) -> None:
    assert naming.parse_snapshot(name) == expected


def test_human_size_stays_readable() -> None:
    assert naming.human_size(512) == "512 B"
    assert naming.human_size(11108352) == "10.59 MiB"
    assert naming.human_size(1713913856) == "1.60 GiB"
