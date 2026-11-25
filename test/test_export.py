"""快照导出。用真实 sqlite3，但只碰临时文件。"""

from __future__ import annotations

import shutil
import sqlite3 as sqlite3_module
from pathlib import Path

import pytest

from websync import export
from websync.errors import WebSyncError

from .conftest import make_database


@pytest.fixture
def sqlite3_bin() -> Path:
    located = shutil.which("sqlite3")
    if located is None:
        pytest.skip("本机没有 sqlite3")
    return Path(located)


def _rows(path: Path) -> int:
    connection = sqlite3_module.connect(path)
    try:
        return connection.execute("select count(*) from chapter").fetchone()[0]
    finally:
        connection.close()


def test_export_produces_readable_copy(tmp_path: Path, sqlite3_bin: Path) -> None:
    source = make_database(tmp_path / "db" / "novel.db", rows=300)
    destination = tmp_path / "out" / "20260101000000-novel.db"

    exported = export.export_snapshot(sqlite3_bin, source, destination)

    assert exported == destination
    assert destination.is_file()
    assert _rows(destination) == 300
    assert not destination.with_name(destination.name + ".part").exists()


def test_export_refuses_missing_source(tmp_path: Path, sqlite3_bin: Path) -> None:
    with pytest.raises(WebSyncError):
        export.export_snapshot(sqlite3_bin, tmp_path / "absent.db", tmp_path / "out.db")


def test_export_reports_unreadable_source(tmp_path: Path, sqlite3_bin: Path) -> None:
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database at all")

    with pytest.raises(WebSyncError):
        export.export_snapshot(sqlite3_bin, broken, tmp_path / "out.db")
