"""TUI 与业务的接线。

只验接线：按键能不能切窗口、进目录，worker 能不能把结果写回界面。
业务细节在 test_ops.py 里已经覆盖，这里不重复断言。
"""

from __future__ import annotations

import asyncio

from textual.widgets import DataTable, Static

from websync import disks, remote
from websync.app import UploadConfirmScreen, WebSyncApp

from .conftest import Sandbox


def _row_index(app: WebSyncApp, table: str, name: str) -> int:
    rows = app.remote_rows if table == "remote" else app.local_rows
    for index, row in enumerate(rows):
        if row.name == name:
            return index
    raise AssertionError(f"{table} 里没有 {name}：{[row.name for row in rows]}")


def test_keys_switch_active_pane(config, sandbox: Sandbox) -> None:
    async def scenario() -> list[str]:
        seen: list[str] = []
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.3)
            seen.append(app.active)
            await pilot.press("2")
            seen.append(app.active)
            await pilot.press("1")
            seen.append(app.active)
        return seen

    assert asyncio.run(scenario()) == ["remote", "local", "remote"]


def test_enter_and_backspace_walk_remote_directories(config, sandbox: Sandbox) -> None:
    (sandbox.remote / "子文件夹").mkdir()

    async def scenario() -> list[str]:
        seen: list[str] = []
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.4)
            seen.append(app.remote_path)
            table = app.query_one("#remote-table", DataTable)
            table.move_cursor(row=_row_index(app, "remote", "子文件夹"))
            await pilot.press("enter")
            await pilot.pause(0.4)
            seen.append(app.remote_path)
            await pilot.press("backspace")
            await pilot.pause(0.4)
            seen.append(app.remote_path)
        return seen

    assert asyncio.run(scenario()) == ["/", "/子文件夹", "/"]


def test_goto_changes_remote_path(config, sandbox: Sandbox) -> None:
    (sandbox.remote / "别处").mkdir()

    async def scenario() -> str:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.4)
            await pilot.press("g")
            await pilot.pause(0.2)
            app.screen.query_one("#path-input").value = "/别处"
            await pilot.click("#ok")
            await pilot.pause(0.4)
            return app.remote_path

    assert asyncio.run(scenario()) == "/别处"


def test_upload_by_key_reaches_remote(config, sandbox: Sandbox) -> None:
    extra = sandbox.db_dir / "z.txt"
    extra.write_text("要传的内容", encoding="utf-8")

    async def scenario() -> bool:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.4)
            app._goto_local(sandbox.db_dir)
            await pilot.pause(0.3)
            app.query_one("#local-table", DataTable).move_cursor(
                row=_row_index(app, "local", "z.txt")
            )
            await pilot.press("u")
            for _ in range(200):
                await pilot.pause(0.05)
                if list(sandbox.remote.glob("*-z.txt")):
                    return True
        return False

    assert asyncio.run(scenario())


def test_upload_folder_by_key_creates_tree(config, sandbox: Sandbox) -> None:
    folder = sandbox.db_dir / "备份"
    (folder / "子目录").mkdir(parents=True)
    (folder / "a.txt").write_text("A", encoding="utf-8")
    (folder / "子目录" / "b.txt").write_text("B", encoding="utf-8")

    async def scenario() -> bool:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.4)
            app._goto_local(sandbox.db_dir)
            await pilot.pause(0.3)
            app.query_one("#local-table", DataTable).move_cursor(
                row=_row_index(app, "local", "备份")
            )
            await pilot.press("u")
            for _ in range(200):
                await pilot.pause(0.05)
                if list(sandbox.remote.glob("*-备份/子目录/b.txt")):
                    return True
        return False

    assert asyncio.run(scenario())


def test_download_by_key_writes_to_local_dir(config, sandbox: Sandbox) -> None:
    stored = sandbox.remote / "20260101000000-novel.db"
    stored.write_bytes((sandbox.db_dir / "novel.db").read_bytes())

    async def scenario() -> bool:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.4)
            await pilot.press("1")
            app.query_one("#remote-table", DataTable).move_cursor(
                row=_row_index(app, "remote", stored.name)
            )
            await pilot.press("d")
            for _ in range(200):
                await pilot.pause(0.05)
                if (app.local_path / stored.name).is_file():
                    return True
        return False

    assert asyncio.run(scenario())


def test_tui_shows_why_remote_listing_failed(config, sandbox: Sandbox, monkeypatch) -> None:
    """只报「读取远端目录失败」等于没说原因，工具的原话要能看到。"""
    monkeypatch.setenv("FAKE_KUAKE_LIST_FAIL", "1")

    async def scenario() -> list[str]:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            for _ in range(100):
                await pilot.pause(0.05)
                if any("list failed" in line for line in app._log_lines):
                    break
            return list(app._log_lines)

    lines = asyncio.run(scenario())

    assert any("list failed" in line for line in lines)


def test_titles_show_both_paths(config, sandbox: Sandbox) -> None:
    async def scenario() -> tuple[str, str, str]:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.3)
            return (
                str(app.query_one("#remote-title").content),
                str(app.query_one("#local-title").content),
                str(app.query_one("#inbox-bar", Static).content),
            )

    remote_title, local_title, bar = asyncio.run(scenario())

    assert "/" in remote_title
    assert str(config.download_dir) in local_title
    assert str(config.download_dir) in bar
    assert str(config.db_dir) in bar


def test_remote_table_shows_time_and_newest_first(config, sandbox: Sandbox) -> None:
    for name in ("20260101000000-novel.db", "20260102000000-report.xlsx"):
        (sandbox.remote / name).write_bytes(b"x")

    async def scenario() -> tuple[list[str], list[str]]:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.5)
            table = app.query_one("#remote-table")
            headers = [str(column.label) for column in table.columns.values()]
            names = [str(table.get_row_at(index)[0]) for index in range(table.row_count)]
            return headers, names

    headers, names = asyncio.run(scenario())

    assert headers == ["名称", "时间", "大小"]
    assert names == ["20260102000000-report.xlsx", "20260101000000-novel.db"]


def test_disk_bar_refreshes_on_a_timer(config, monkeypatch) -> None:
    """把间隔压到 0.05 秒，验证定时器真的在跑，而不是只画一次。"""
    ticks: list[int] = []
    monkeypatch.setattr(disks, "collect", lambda: ticks.append(1) or [])
    monkeypatch.setattr(disks, "REFRESH_SECONDS", 0.05)

    async def scenario() -> None:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.6)

    asyncio.run(scenario())

    assert len(ticks) >= 2


def test_task_row_id_is_safe_for_textual() -> None:
    """文件名含点号，textual 的 id 不允许点号。"""
    identifier = WebSyncApp.task_id("20261029100147-novel.db")

    assert "." not in identifier
    assert identifier.startswith("task-")


def test_remote_entry_knows_directories() -> None:
    assert remote.RemoteEntry(name="子目录", size=0, is_dir=True).is_dir is True
    assert remote.RemoteEntry(name="x.txt", size=1).is_dir is False


def test_local_pane_hides_sqlite_temp_files(config, sandbox: Sandbox) -> None:
    """-wal / -shm 是 SQLite 的运行期文件，不是用户要传的东西。"""
    (sandbox.db_dir / "novel.db-wal").write_bytes(b"x")
    (sandbox.db_dir / "novel.db-shm").write_bytes(b"x")

    async def scenario() -> list[str]:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.4)
            app._goto_local(sandbox.db_dir)
            await pilot.pause(0.3)
            return [row.name for row in app.local_rows]

    names = asyncio.run(scenario())

    assert "novel.db" in names
    assert "novel.db-wal" not in names
    assert "novel.db-shm" not in names


def test_only_1_and_2_switch_panes(config, sandbox: Sandbox) -> None:
    """没有第三个窗口键。上传目录靠本地窗口自己走进去。"""
    keys = {binding.key for binding in WebSyncApp.BINDINGS}

    assert "1" in keys
    assert "2" in keys
    assert "3" not in keys


def test_download_dir_is_created_on_start(config, sandbox: Sandbox) -> None:
    assert not config.download_dir.exists()

    async def scenario() -> bool:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.3)
            return config.download_dir.is_dir()

    assert asyncio.run(scenario())


def test_navigation_is_written_to_history(config, sandbox: Sandbox) -> None:
    (sandbox.remote / "别处").mkdir()

    async def scenario() -> list[str]:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.4)
            app._goto_remote("/别处")
            await pilot.pause(0.3)
            return [row.path for row in app.history.recent_dirs()]

    paths = asyncio.run(scenario())

    assert "/别处" in paths
    assert str(config.download_dir.expanduser()) in paths


def test_history_key_opens_screen_and_jumps(config, sandbox: Sandbox) -> None:
    (sandbox.remote / "别处").mkdir()

    async def scenario() -> str:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.4)
            app._goto_remote("/别处")
            await pilot.pause(0.3)
            app._goto_remote("/")
            await pilot.pause(0.3)
            await pilot.press("h")
            await pilot.pause(0.3)
            table = app.screen.query_one("#history-table", DataTable)
            for index in range(table.row_count):
                if "/别处" in str(table.get_row_at(index)[1]):
                    table.move_cursor(row=index)
                    break
            await pilot.press("enter")
            await pilot.pause(0.4)
            return app.remote_path

    assert asyncio.run(scenario()) == "/别处"


def test_upload_is_replayable_from_history(config, sandbox: Sandbox) -> None:
    extra = sandbox.db_dir / "z.txt"
    extra.write_text("要传的内容", encoding="utf-8")

    async def scenario() -> int:
        app = WebSyncApp(config)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.4)
            app._goto_local(sandbox.db_dir)
            await pilot.pause(0.3)
            first = app.run_upload(extra, "/")
            for _ in range(200):
                await pilot.pause(0.05)
                if list(sandbox.remote.glob("*-z.txt")):
                    break
            await pilot.pause(0.2)
            await pilot.press("h")
            await pilot.pause(0.3)
            table = app.screen.query_one("#history-table", DataTable)
            for index in range(table.row_count):
                if "z.txt" in str(table.get_row_at(index)[1]):
                    table.move_cursor(row=index)
                    break
            await pilot.press("enter")
            for _ in range(200):
                await pilot.pause(0.05)
                if len(list(sandbox.remote.glob("*-z.txt"))) >= 2:
                    break
            return len(list(sandbox.remote.glob("*-z.txt")))

    assert asyncio.run(scenario()) >= 2


def test_hint_bar_explains_snapshot_for_sqlite_selection(config, sandbox: Sandbox) -> None:
    """选中库文件时，底栏要先说清 u 会导出快照，用户才敢按。"""

    async def scenario() -> str:
        app = WebSyncApp(config)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause(0.4)
            app._goto_local(sandbox.db_dir)
            await pilot.pause(0.3)
            await pilot.press("2")
            app.query_one("#local-table", DataTable).move_cursor(
                row=_row_index(app, "local", "novel.db")
            )
            await pilot.pause(0.2)
            return str(app.query_one("#hint-bar", Static).content)

    bar = asyncio.run(scenario())

    assert "导出快照" in bar
    assert "trans-app 不用停" in bar


def test_hint_bar_says_plain_upload_for_other_files(config, sandbox: Sandbox) -> None:
    extra = sandbox.db_dir / "z.txt"
    extra.write_text("要传的内容", encoding="utf-8")

    async def scenario() -> str:
        app = WebSyncApp(config)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause(0.4)
            app._goto_local(sandbox.db_dir)
            await pilot.pause(0.3)
            await pilot.press("2")
            app.query_one("#local-table", DataTable).move_cursor(
                row=_row_index(app, "local", "z.txt")
            )
            await pilot.pause(0.2)
            return str(app.query_one("#hint-bar", Static).content)

    bar = asyncio.run(scenario())

    assert "原样上传" in bar
    assert "不做快照" in bar


def test_sqlite_upload_asks_before_exporting(config, sandbox: Sandbox) -> None:
    """按 u 先弹确认，讲清 sqlite3 .backup、完整性检查与远端文件名。"""

    async def scenario() -> tuple[bool, str, list[str], bool]:
        app = WebSyncApp(config)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause(0.4)
            app._goto_local(sandbox.db_dir)
            await pilot.pause(0.3)
            app.query_one("#local-table", DataTable).move_cursor(
                row=_row_index(app, "local", "novel.db")
            )
            await pilot.press("u")
            await pilot.pause(0.4)
            opened = isinstance(app.screen, UploadConfirmScreen)
            body = str(app.screen.query_one("#confirm-body", Static).content) if opened else ""
            await pilot.press("escape")
            await pilot.pause(0.4)
            return opened, body, list(sandbox.remote.glob("*-novel.db")), app.busy

    opened, body, uploaded, busy = asyncio.run(scenario())

    assert opened
    assert "sqlite3 .backup" in body
    assert "integrity_check" in body
    assert ".sha256" in body
    assert uploaded == []
    assert busy is False


def test_sqlite_upload_runs_after_confirm(config, sandbox: Sandbox) -> None:
    async def scenario() -> bool:
        app = WebSyncApp(config)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause(0.4)
            app._goto_local(sandbox.db_dir)
            await pilot.pause(0.3)
            app.query_one("#local-table", DataTable).move_cursor(
                row=_row_index(app, "local", "novel.db")
            )
            await pilot.press("u")
            await pilot.pause(0.3)
            await pilot.press("y")
            for _ in range(200):
                await pilot.pause(0.05)
                if list(sandbox.remote.glob("*-novel.db")):
                    return True
        return False

    assert asyncio.run(scenario())


def test_help_key_explains_export(config, sandbox: Sandbox) -> None:
    """「导出 db 没有命令」这件事要能在界面里查到。"""

    async def scenario() -> tuple[str, str]:
        app = WebSyncApp(config)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause(0.4)
            await pilot.press("question_mark")
            await pilot.pause(0.3)
            title = str(app.screen.query_one("#dialog-title", Static).content)
            body = str(app.screen.query_one("#help-body", Static).content)
            return title, body

    title, body = asyncio.run(scenario())

    assert "说明" in title
    assert "推库（本机 → 网盘）" in body
    assert str(sandbox.db_dir) in body
    assert "没有单独命令" in body
    assert "sqlite3 .backup" in body
    assert "改名为 novel.db" in body


def test_confirm_notes_when_staging_space_is_short(tmp_path) -> None:
    source = tmp_path / "novel.db"
    source.write_bytes(b"x" * 1024)

    short = UploadConfirmScreen(source, "/", 512, "20260101000000-novel.db")._free_note(1024)
    unknown = UploadConfirmScreen(source, "/", None, "20260101000000-novel.db")._free_note(1024)

    assert "余量不足" in short
    assert "未取到" in unknown
