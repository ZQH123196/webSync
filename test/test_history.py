"""历史的记录与截断。纯文件操作，不碰 UI。"""

from __future__ import annotations

import json
from pathlib import Path

from websync import history


def test_record_dir_counts_and_floats_up(tmp_path: Path) -> None:
    store = history.History(path=tmp_path / "history.json")
    store.record_dir(history.PANE_REMOTE, "/a")
    store.record_dir(history.PANE_REMOTE, "/b")
    store.record_dir(history.PANE_REMOTE, "/a")

    assert [entry.path for entry in store.recent_dirs()] == ["/a", "/b"]
    assert store.recent_dirs()[0].count == 2


def test_two_panes_are_tracked_separately(tmp_path: Path) -> None:
    store = history.History(path=tmp_path / "history.json")
    store.record_dir(history.PANE_REMOTE, "/远端")
    store.record_dir(history.PANE_LOCAL, "/home/zqh")

    assert {entry.path for entry in store.recent_dirs()} == {"/远端", "/home/zqh"}


def test_dir_history_is_capped(tmp_path: Path) -> None:
    store = history.History(path=tmp_path / "history.json")
    for index in range(history.DIR_LIMIT + 10):
        store.record_dir(history.PANE_REMOTE, f"/d{index}")

    assert len(store.dirs) == history.DIR_LIMIT


def test_transfer_history_keeps_latest_first(tmp_path: Path) -> None:
    store = history.History(path=tmp_path / "history.json")
    store.record_transfer(history.KIND_UPLOAD, "/db/a.db", "/远端", "a.db")
    store.record_transfer(history.KIND_DOWNLOAD, "/down", "/远端", "b.db")

    assert [entry.name for entry in store.recent_transfers()] == ["b.db", "a.db"]


def test_same_transfer_is_not_duplicated(tmp_path: Path) -> None:
    store = history.History(path=tmp_path / "history.json")
    store.record_transfer(history.KIND_UPLOAD, "/db/a.db", "/远端", "a.db")
    store.record_transfer(history.KIND_UPLOAD, "/db/a.db", "/远端", "a.db")

    assert len(store.transfers) == 1


def test_save_and_reopen_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    store = history.History(path=path)
    store.record_dir(history.PANE_LOCAL, "/tmp/x")
    store.record_transfer(history.KIND_DOWNLOAD, "/down", "/远端", "x.db")
    store.save()

    reopened = history.History.open(path)

    assert [entry.path for entry in reopened.recent_dirs()] == ["/tmp/x"]
    assert [entry.name for entry in reopened.recent_transfers()] == ["x.db"]


def test_broken_file_is_reported_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("这不是 JSON", encoding="utf-8")

    store = history.History.open(path)

    assert store.broken
    assert store.dirs == []


def test_unknown_entries_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text(
        json.dumps(
            {
                "dirs": [{"pane": "远端", "path": "/x"}, {"pane": "remote", "path": "/y"}],
                "transfers": [{"kind": "move", "local": "", "remote_dir": "", "name": "x"}],
            }
        ),
        encoding="utf-8",
    )

    store = history.History.open(path)

    assert [entry.path for entry in store.dirs] == ["/y"]
    assert store.transfers == []
