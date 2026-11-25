"""交互式主界面。

左边远端，右边本地。``1`` / ``2`` 切换当前窗口，回车进入目录，退格回上级。
上传永远取本地窗口的选中项，下载永远取远端窗口的选中项。

渲染层只负责布局与按键，不碰子进程。所有动作都经 :mod:`websync.ops`。
阻塞调用一律走 textual worker 的线程模式，进度靠 ``call_from_thread`` 回主线程。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    Static,
)

from . import disks, export, history, ops, remote
from .config import Config, is_visible
from .errors import KIND_TRANSFER, Cancelled, WebSyncError
from .guide import build_help_text
from .naming import BEIJING, human_size, make_prefix, snapshot_name
from .transfer import CancelToken, Progress

LOG_LINES = 8
CELL_LIMIT = 48

CSS = """
Screen { layout: vertical; }
#disk-bar { height: auto; padding: 0 1; border-bottom: solid $primary; }
#panels { height: 1fr; }
#remote-panel { width: 1fr; border: round $primary-darken-2; }
#local-panel { width: 1fr; border: round $primary-darken-2; }
#remote-panel.active { border: round $accent; }
#local-panel.active { border: round $accent; }
.panel-title { padding: 0 1; text-style: bold; }
#transfer-panel { height: auto; max-height: 12; border: round $secondary; }
#transfer-title { padding: 0 1; text-style: bold; }
#transfer-log { padding: 0 1; height: auto; }
#inbox-bar { height: 1; padding: 0 1; color: $text-muted; }
#hint-bar { height: 1; padding: 0 1; color: $accent; }
TaskRow { height: 3; padding: 0 1; }
TaskRow Label { height: 1; }
TaskRow ProgressBar { height: 1; }
#dialog-wide { width: 96; max-width: 100%; height: auto; max-height: 24; border: thick $primary; background: $surface; padding: 1 2; }
#history-hint { padding-top: 1; color: $text-muted; }
#dialog { width: 72; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
#dialog-title { text-style: bold; padding-bottom: 1; }
#dialog-actions { height: auto; align-horizontal: right; padding-top: 1; }
#dialog-actions Button { margin-left: 2; }
#confirm-body { padding-bottom: 1; }
#confirm-file { text-style: bold; }
#confirm-note { color: $text-muted; padding-top: 1; }
#help-dialog { width: 96; max-width: 100%; height: 90%; max-height: 44; border: thick $primary; background: $surface; padding: 1 2; }
#help-scroll { height: 1fr; }
#help-body { height: auto; }
"""


@dataclass(frozen=True)
class Row:
    """表格里的一行。``path`` 对本地是 Path，对远端是远端路径字符串。"""

    name: str
    path: object
    is_dir: bool
    size: int = 0
    mtime: float = 0.0


class TaskRow(Vertical):
    """一条传输任务的进度行。"""

    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="task-title")
        yield ProgressBar(total=100, show_eta=False)

    def update_progress(self, progress: Progress) -> None:
        self.query_one(ProgressBar).update(total=100, progress=min(progress.percent, 100.0))
        parts = [f"{progress.percent:5.1f}%"]
        if progress.speed:
            parts.append(progress.speed)
        if progress.eta:
            parts.append(f"剩余 {progress.eta}")
        self.query_one(Label).update(f"{self._title}  " + "  ".join(parts))


class PathScreen(ModalScreen[Path | None]):
    """填一个路径：跳转与下载目录都用它。"""

    BINDINGS = [Binding("escape", "dismiss(None)", "取消")]

    def __init__(self, title: str, default: str) -> None:
        super().__init__()
        self._title = title
        self._default = default

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title, id="dialog-title")
            yield Input(value=self._default, id="path-input")
            with Horizontal(id="dialog-actions"):
                yield Button("确定", variant="primary", id="ok")
                yield Button("取消", variant="default", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        raw = self.query_one("#path-input", Input).value.strip()
        self.dismiss(Path(raw).expanduser() if raw else None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        self.dismiss(Path(raw).expanduser() if raw else None)


@dataclass(frozen=True)
class HistoryChoice:
    """历史里选中的一条。按 kind 决定怎么执行。"""

    kind: str
    pane: str = ""
    path: str = ""
    local: str = ""
    remote_dir: str = ""
    name: str = ""


class HistoryScreen(ModalScreen[HistoryChoice | None]):
    """最近用过的目录与传输。回车执行，Esc 关掉。"""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "关闭"),
        Binding("h", "dismiss(None)", "关闭"),
    ]

    def __init__(self, rows: list[tuple[HistoryChoice, str, str, str, str]]) -> None:
        super().__init__()
        self._rows = rows

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-wide"):
            yield Label("历史（回车执行）", id="dialog-title")
            yield DataTable(id="history-table", cursor_type="row")
            yield Label("目录回车跳过去，传输回车重做一遍。", id="history-hint")

    def on_mount(self) -> None:
        table = self.query_one("#history-table", DataTable)
        table.add_columns("类型", "内容", "次数", "最近")
        for index, (_choice, kind, content, count, when) in enumerate(self._rows):
            table.add_row(kind, self._cell(content), count, when, key=str(index))
        if self._rows:
            table.move_cursor(row=0)
            table.focus()

    @staticmethod
    def _cell(text: str) -> str:
        return text if len(text) <= 60 else text[:59] + "…"

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        index = int(event.row_key.value)
        self.dismiss(self._rows[index][0])


class HelpScreen(ModalScreen[None]):
    """按 ? 打开的说明。操作步骤在前，机制解释在后。"""

    BINDINGS = [
        Binding("escape", "close", "关闭"),
        Binding("q", "close", "关闭"),
        Binding("question_mark", "close", "关闭"),
    ]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Label("WebSync 说明（Esc 关闭，方向键滚动）", id="dialog-title")
            with VerticalScroll(id="help-scroll"):
                yield Static(escape(build_help_text(self._config)), id="help-body")
            yield Label("按 h 看历史，按 ? 回到这里。", id="history-hint")

    def on_mount(self) -> None:
        self.query_one("#help-scroll", VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)


class UploadConfirmScreen(ModalScreen[bool]):
    """SQLite 文件上传前的确认。

    上传一个库要几分钟，用户需要先知道会导出快照、远端叫什么名字。
    普通文件不经过这里，保持一次按键就传。
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消"),
        Binding("q", "cancel", "取消"),
        Binding("n", "cancel", "取消"),
        Binding("y", "confirm", "导出并上传"),
    ]

    def __init__(
        self,
        source: Path,
        remote_dir: str,
        free_bytes: int | None,
        preview_name: str,
    ) -> None:
        super().__init__()
        self._source = source
        self._remote_dir = remote_dir
        self._free_bytes = free_bytes
        self._preview_name = preview_name

    def _free_note(self, size: int) -> str:
        if self._free_bytes is None:
            return f"暂存需 {human_size(size)} 余量，余量未取到。不足时会直接报错。"
        if self._free_bytes < size:
            return (
                f"暂存需 {human_size(size)}，当前可用 {human_size(self._free_bytes)}："
                "余量不足，导出会失败。"
            )
        return f"暂存需 {human_size(size)} 余量，当前可用 {human_size(self._free_bytes)}。"

    def compose(self) -> ComposeResult:
        size = self._source.stat().st_size
        lines = [
            "1. 用 sqlite3 .backup 导出一致快照。源库照常读写，trans-app 不用停。",
            "2. 快照通过 PRAGMA integrity_check 才上传，坏库不传。",
            f"3. 远端名形如 {self._preview_name}，并附 .sha256 校验文件。",
            "4. 上传后核对远端字节数。远端有同名文件直接拒绝，不覆盖。",
            self._free_note(size),
        ]
        with Vertical(id="dialog-wide"):
            yield Label("上传前确认", id="dialog-title")
            yield Label(
                f"{self._source.name}（{human_size(size)}）是 SQLite 库。",
                id="confirm-file",
            )
            yield Static(escape("\n".join(lines)), id="confirm-body")
            yield Label(f"目标远端目录：{self._remote_dir}", id="confirm-note")
            with Horizontal(id="dialog-actions"):
                yield Button("导出并上传", variant="primary", id="confirm")
                yield Button("取消", variant="default", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#confirm", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class WebSyncApp(App[None]):
    TITLE = "WebSync"
    CSS = CSS

    BINDINGS = [
        Binding("1", "focus_remote", "远端"),
        Binding("2", "focus_local", "本地"),
        Binding("u", "upload", "上传"),
        Binding("d", "download", "下载"),
        Binding("enter", "enter", "进入"),
        Binding("backspace", "up", "上级"),
        Binding("g", "goto", "跳转"),
        Binding("h", "history", "历史"),
        Binding("r", "refresh", "刷新"),
        Binding("question_mark", "help", "说明"),
        Binding("escape", "cancel", "中断"),
        Binding("q", "quit", "退出"),
    ]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.toolset = ops.resolve_tools(config)
        self.remote_path = config.remote_dir
        self.local_path = config.start_dir()
        self.remote_rows: list[Row] = []
        self.local_rows: list[Row] = []
        self.active = "remote"
        self.busy = False
        self.cancel_token = CancelToken()
        self.history = history.History.open()
        self._log_lines: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("磁盘信息加载中…", id="disk-bar")
        with Horizontal(id="panels"):
            with Vertical(id="remote-panel"):
                yield Label("", id="remote-title", classes="panel-title")
                yield DataTable(id="remote-table", cursor_type="row")
            with Vertical(id="local-panel"):
                yield Label("", id="local-title", classes="panel-title")
                yield DataTable(id="local-table", cursor_type="row")
        yield Static("", id="hint-bar")
        yield Static("", id="inbox-bar")
        with VerticalScroll(id="transfer-panel"):
            yield Label("传输", id="transfer-title")
            yield Static("", id="transfer-log")
        yield Footer()

    def on_mount(self) -> None:
        # 下载目录第一次可能还不存在。建不出来就留给列表去报错，不吞掉。
        try:
            self.local_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.query_one("#remote-table", DataTable).add_columns("名称", "时间", "大小")
        self.query_one("#local-table", DataTable).add_columns("名称", "时间", "大小")
        self._remember_dir(history.PANE_REMOTE, self.remote_path)
        self._remember_dir(history.PANE_LOCAL, str(self.local_path))
        self._render_titles()
        self._set_active("remote")
        self.refresh_remote()
        self.refresh_local()
        self._refresh_disks()
        self.set_interval(disks.REFRESH_SECONDS, self._refresh_disks)

    # ---------------- 标题与高亮 ----------------

    def _render_titles(self) -> None:
        self.query_one("#remote-title", Label).update(escape(f"远端 1 · {self.remote_path}"))
        self.query_one("#local-title", Label).update(escape(f"本地 2 · {self.local_path}"))
        self._update_inbox()

    def _update_inbox(self, pane: str | None = None) -> None:
        """底栏回答两件事：库在哪个目录，选中的东西按键会发生什么。

        两行分开写：路径可能很长，混在一行会把提示挤掉。
        """
        upload = self.config.db_dir or "未配置"
        paths = f"上传目录: {upload}    下载到 {self.local_path}"
        self.query_one("#inbox-bar", Static).update(escape(paths))
        self.query_one("#hint-bar", Static).update(
            escape(self._selection_hint(pane or self.active))
        )

    def _selection_hint(self, pane: str) -> str:
        """按选中项说明这一键的后果。用户不用去翻文档。"""
        if pane == "remote":
            row = self.selected_remote()
            if row is None:
                return "d 下载选中项"
            if row.name == "..":
                return ".. 是上级目录：按 Enter"
            return f"选中 {row.name}：按 d 下载到本地当前目录"
        row = self.selected_local()
        if row is None:
            return "按 g 填上传目录，选中文件或目录后按 u"
        if row.name == "..":
            return ".. 是上级目录：按 Enter"
        if row.is_dir:
            return f"选中 {row.name}/：按 u 整个目录上传"
        if export.is_sqlite(Path(str(row.path))):
            return f"选中 {row.name}：按 u 导出快照再上传，trans-app 不用停"
        return f"选中 {row.name}：按 u 原样上传，不做快照"

    def _set_active(self, which: str) -> None:
        self.active = which
        for name, panel in (("remote", "#remote-panel"), ("local", "#local-panel")):
            self.query_one(panel).set_class(name == which, "active")
        table = "#remote-table" if which == "remote" else "#local-table"
        self.query_one(table, DataTable).focus()
        self._update_inbox()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """光标一动就换底栏提示。只认当前窗口，后台刷新不抢提示。"""
        pane = "remote" if event.data_table.id == "remote-table" else "local"
        if event.data_table.id in ("remote-table", "local-table") and pane == self.active:
            self._update_inbox(pane)

    # ---------------- 渲染 ----------------

    def _log(self, message: str) -> None:
        """追加一行，保留最近若干行。steps 与 problems 都能看到。"""
        for line in str(message).splitlines():
            self._log_lines.append(line)
        del self._log_lines[:-LOG_LINES]
        self.query_one("#transfer-log", Static).update(escape("\n".join(self._log_lines)))

    @staticmethod
    def _stamp(mtime: float) -> str:
        if not mtime:
            return "-"
        return datetime.fromtimestamp(mtime, tz=BEIJING).strftime("%m-%d %H:%M:%S")

    @staticmethod
    def _cell(text: str) -> str:
        return text if len(text) <= CELL_LIMIT else text[: CELL_LIMIT - 1] + "…"

    def _fill(self, table_id: str, rows: list[Row]) -> None:
        table = self.query_one(table_id, DataTable)
        table.clear()
        for index, row in enumerate(rows):
            size = "-" if row.is_dir else human_size(row.size)
            name = f"[{row.name}]" if row.is_dir and row.name != ".." else row.name
            table.add_row(self._cell(name), self._stamp(row.mtime), size, key=str(index))
        if rows:
            table.move_cursor(row=0)

    def _apply_remote(self, rows: list[Row]) -> None:
        self.remote_rows = rows
        self._fill("#remote-table", rows)
        self._update_inbox()
        if len(rows) <= 1:
            self._log(f"远端目录是空的：{self.remote_path}")

    def _apply_local(self, rows: list[Row]) -> None:
        self.local_rows = rows
        self._fill("#local-table", rows)
        self._update_inbox()

    # ---------------- 读目录 ----------------

    @work(thread=True, exclusive=True, group="remote")
    def refresh_remote(self) -> None:
        try:
            entries = ops.list_remote(self.config, self.toolset, self.remote_path)
        except WebSyncError as exc:
            self.call_from_thread(self._log, exc.message)
            for problem in exc.problems:
                self.call_from_thread(self._log, f"  {problem}")
            return
        rows: list[Row] = []
        if self.remote_path != "/":
            rows.append(Row("..", remote.parent(self.remote_path), True))
        for entry in entries:
            rows.append(
                Row(
                    entry.name,
                    remote.join(self.remote_path, entry.name),
                    entry.is_dir,
                    entry.size,
                    entry.mtime,
                )
            )
        self.call_from_thread(self._apply_remote, rows)

    @work(thread=True, exclusive=True, group="local")
    def refresh_local(self) -> None:
        try:
            rows = self._read_local()
        except WebSyncError as exc:
            self.call_from_thread(self._log, exc.message)
            for problem in exc.problems:
                self.call_from_thread(self._log, f"  {problem}")
            return
        self.call_from_thread(self._apply_local, rows)

    def _read_local(self) -> list[Row]:
        base = self.local_path
        try:
            children = [entry for entry in base.iterdir() if is_visible(entry.name)]
        except OSError as exc:
            raise WebSyncError(KIND_TRANSFER, f"读不到目录：{base}", problems=[str(exc)]) from exc

        rows: list[Row] = []
        if base.parent != base:
            rows.append(Row("..", base.parent, True))
        directories = sorted((p for p in children if p.is_dir()), key=lambda p: p.name)
        files = [p for p in children if p.is_file()]
        files.sort(key=self._mtime, reverse=True)
        for path in [*directories, *files]:
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append(Row(path.name, path, path.is_dir(), stat.st_size, stat.st_mtime))
        return rows

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    # ---------------- 选中项 ----------------

    def _selected(self, rows: list[Row], table_id: str) -> Row | None:
        table = self.query_one(table_id, DataTable)
        if table.row_count == 0 or table.cursor_row < 0:
            return None
        index = table.cursor_row
        return rows[index] if index < len(rows) else None

    def selected_remote(self) -> Row | None:
        return self._selected(self.remote_rows, "#remote-table")

    def selected_local(self) -> Row | None:
        return self._selected(self.local_rows, "#local-table")

    # ---------------- 动作 ----------------

    def action_focus_remote(self) -> None:
        self._set_active("remote")

    def action_focus_local(self) -> None:
        self._set_active("local")

    def action_refresh(self) -> None:
        if self.active == "remote":
            self.refresh_remote()
        else:
            self.refresh_local()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """回车进目录。

        DataTable 自己吃掉回车并发出 RowSelected，所以这里接事件而不是绑按键。
        按哪个窗口的回车就动哪个窗口，同时把它切成当前窗口。
        """
        table_id = event.data_table.id
        self._set_active("remote" if table_id == "remote-table" else "local")
        self.action_enter()

    def action_enter(self) -> None:
        if self.active == "remote":
            row = self.selected_remote()
            if row is None:
                return
            if row.name == ".." or row.is_dir:
                self._goto_remote(str(row.path))
            return
        row = self.selected_local()
        if row is None:
            return
        if row.name == ".." or row.is_dir:
            self._goto_local(Path(str(row.path)))

    def action_up(self) -> None:
        if self.active == "remote":
            self._goto_remote(remote.parent(self.remote_path))
        else:
            self._goto_local(self.local_path.parent)

    def _goto_remote(self, path: str) -> None:
        self.remote_path = path
        self._remember_dir(history.PANE_REMOTE, path)
        self._render_titles()
        self.refresh_remote()

    def _goto_local(self, path: Path) -> None:
        self.local_path = path
        self._remember_dir(history.PANE_LOCAL, str(path))
        self._render_titles()
        self.refresh_local()

    def _remember_dir(self, pane: str, path: str) -> None:
        self.history.record_dir(pane, path)
        self.history.save()

    def _remember_transfer(self, kind: str, local: str, remote_dir: str, name: str) -> None:
        self.history.record_transfer(kind, local, remote_dir, name)
        self.history.save()

    def action_goto(self) -> None:
        if self.active == "remote":
            default, title = self.remote_path, "跳到远端目录"
        else:
            default, title = str(self.local_path), "跳到本地目录"
        self.push_screen(PathScreen(title, default), self._after_goto)

    def _after_goto(self, target: Path | None) -> None:
        if target is None:
            return
        if self.active == "remote":
            self._goto_remote(str(target))
        else:
            self._goto_local(target)

    def action_history(self) -> None:
        rows = self._history_rows()
        if not rows:
            self.notify("还没有历史", severity="warning")
            return
        self.push_screen(HistoryScreen(rows), self._apply_history)

    def _history_rows(self) -> list[tuple[HistoryChoice, str, str, str, str]]:
        rows: list[tuple[HistoryChoice, str, str, str, str]] = []
        for entry in self.history.recent_transfers():
            when = self._stamp(entry.last)
            if entry.kind == history.KIND_UPLOAD:
                content = f"↑ {entry.name} → {entry.remote_dir}"
                choice = HistoryChoice(
                    kind=history.KIND_UPLOAD,
                    local=entry.local,
                    remote_dir=entry.remote_dir,
                    name=entry.name,
                )
                rows.append((choice, "上传", content, "-", when))
            else:
                content = f"↓ {entry.name} → {entry.local}"
                choice = HistoryChoice(
                    kind=history.KIND_DOWNLOAD,
                    local=entry.local,
                    remote_dir=entry.remote_dir,
                    name=entry.name,
                )
                rows.append((choice, "下载", content, "-", when))
        for entry in self.history.recent_dirs():
            pane = "远端" if entry.pane == history.PANE_REMOTE else "本地"
            choice = HistoryChoice(kind="dir", pane=entry.pane, path=entry.path)
            rows.append((choice, pane, entry.path, str(entry.count), self._stamp(entry.last)))
        return rows

    def _apply_history(self, choice: HistoryChoice | None) -> None:
        if choice is None:
            return
        if choice.kind == "dir":
            if choice.pane == history.PANE_REMOTE:
                self._set_active("remote")
                self._goto_remote(choice.path)
            else:
                self._set_active("local")
                self._goto_local(Path(choice.path))
            return
        if self.busy:
            self.notify("已有任务在跑", severity="warning")
            return
        if choice.kind == history.KIND_UPLOAD:
            source = Path(choice.local)
            if not source.exists():
                self.notify(f"本地已经没有：{source}", severity="error")
                return
            self._set_active("local")
            self._request_upload(source, choice.remote_dir)
        elif choice.kind == history.KIND_DOWNLOAD:
            self._set_active("remote")
            entry = remote.RemoteEntry(name=choice.name, size=0)
            self.run_download(entry, choice.remote_dir, Path(choice.local))

    def action_cancel(self) -> None:
        if self.busy:
            self.cancel_token.cancel()
            self._log("正在中断……")
        else:
            self._log("当前没有传输任务")

    def action_quit(self) -> None:
        """传输中退出要先把子进程杀掉，否则 kuake 会变成孤儿进程继续上传。"""
        if self.busy:
            self.cancel_token.cancel()
        self.exit()

    def action_help(self) -> None:
        self.push_screen(HelpScreen(self.config))

    def action_upload(self) -> None:
        if self.busy:
            self.notify("已有任务在跑", severity="warning")
            return
        row = self.selected_local()
        if row is None or row.name == "..":
            hint = f"先在本地窗口选文件。上传目录：{self.config.db_dir or '未配置'}"
            self.notify(hint, severity="warning")
            return
        self._request_upload(Path(str(row.path)))

    def _request_upload(self, source: Path, remote_dir: str | None = None) -> None:
        """SQLite 文件先解释再传，其他文件一次按键就传。"""
        if not source.is_dir() and export.is_sqlite(source):
            self._confirm_snapshot_upload(source, remote_dir or self.remote_path)
            return
        self.run_upload(source, remote_dir)

    def _confirm_snapshot_upload(self, source: Path, remote_dir: str) -> None:
        """把「导出」这件事说清楚：谁来导、导到哪、远端叫什么名字。"""
        try:
            free_bytes: int | None = ops.staging_free()
        except OSError:
            free_bytes = None
        screen = UploadConfirmScreen(
            source=source,
            remote_dir=remote_dir,
            free_bytes=free_bytes,
            preview_name=snapshot_name(make_prefix(), source.name),
        )
        self.push_screen(
            screen,
            lambda confirmed: self.run_upload(source, remote_dir) if confirmed else None,
        )

    def action_download(self) -> None:
        if self.busy:
            self.notify("已有任务在跑", severity="warning")
            return
        row = self.selected_remote()
        if row is None or row.name == "..":
            self.notify("先在远端窗口选一个文件或文件夹", severity="warning")
            return
        entry = remote.RemoteEntry(name=row.name, size=row.size, is_dir=row.is_dir, mtime=row.mtime)
        self.run_download(entry, self.remote_path)

    # ---------------- worker ----------------

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if not busy:
            self.cancel_token = CancelToken()

    def _finish_worker(self) -> None:
        """线程结束一定复位，否则一次 OSError 会把界面钉在 busy。"""
        try:
            self.call_from_thread(self._set_busy, False)
        except RuntimeError:
            return

    @staticmethod
    def task_key(name: str, token: str) -> str:
        """进度行的唯一键。重做同一次传输时不能撞上上一行。"""
        return f"{name}-{token}"

    @staticmethod
    def task_id(key: str) -> str:
        """文件名含点号，textual 的 id 只允许字母数字下划线连字符。"""
        return "task-" + re.sub(r"[^A-Za-z0-9_-]", "_", key)

    def _clear_task_rows(self) -> None:
        panel = self.query_one("#transfer-panel", VerticalScroll)
        for child in list(panel.children):
            if child.id and child.id.startswith("task-"):
                child.remove()

    def _add_task_row(self, key: str, title: str) -> TaskRow:
        panel = self.query_one("#transfer-panel", VerticalScroll)
        row = TaskRow(title)
        row.id = self.task_id(key)
        panel.mount(row)
        return row

    def _on_progress(self, key: str, progress: Progress) -> None:
        try:
            row = self.query_one(f"#{self.task_id(key)}", TaskRow)
        except Exception:  # noqa: BLE001 - 行还没挂上时忽略本次进度
            return
        row.update_progress(progress)

    @work(thread=True, exclusive=True, group="transfer")
    def run_upload(self, source: Path, remote_dir: str | None = None) -> None:
        target_dir = remote_dir or self.remote_path
        self.call_from_thread(self._set_busy, True)
        self.call_from_thread(self._clear_task_rows)
        self.cancel_token = CancelToken()
        token = self.cancel_token
        key = self.task_key(source.name, uuid.uuid4().hex[:8])
        self.call_from_thread(self._add_task_row, key, source.name)
        try:
            if source.is_dir():
                outcomes = ops.push_tree(
                    self.config,
                    self.toolset,
                    source,
                    target_dir,
                    prefix=make_prefix(),
                    on_progress=lambda progress: self.call_from_thread(
                        self._on_progress, key, progress
                    ),
                    on_step=lambda message: self.call_from_thread(self._log, message),
                    cancel=token,
                )
                self.call_from_thread(
                    self._log, f"{source.name} 完成：{len(outcomes)} 个文件"
                )
            else:
                outcome = ops.push(
                    self.config,
                    self.toolset,
                    source,
                    target_dir,
                    prefix=make_prefix(),
                    on_progress=lambda progress: self.call_from_thread(
                        self._on_progress, key, progress
                    ),
                    on_step=lambda message: self.call_from_thread(self._log, message),
                    cancel=token,
                )
                self.call_from_thread(
                    self._log,
                    f"{outcome.name} 完成：{human_size(outcome.size)}，"
                    f"sha256 {outcome.sha256[:12]}…",
                )
            self.call_from_thread(
                self._remember_transfer,
                history.KIND_UPLOAD,
                str(source),
                target_dir,
                source.name,
            )
        except Cancelled:
            self.call_from_thread(self._log, f"{source.name} 已中断")
        except WebSyncError as exc:
            self.call_from_thread(self._log, f"{source.name} 失败：{exc.message}")
            for problem in exc.problems:
                self.call_from_thread(self._log, f"  {problem}")
        finally:
            self._finish_worker()
        self.call_from_thread(self.refresh_remote)

    @work(thread=True, exclusive=True, group="transfer")
    def run_download(
        self,
        entry: remote.RemoteEntry,
        remote_dir: str,
        local_dir: Path | None = None,
    ) -> None:
        target_dir = local_dir or self.local_path
        self.call_from_thread(self._set_busy, True)
        self.call_from_thread(self._clear_task_rows)
        self.cancel_token = CancelToken()
        token = self.cancel_token
        key = self.task_key(entry.name, uuid.uuid4().hex[:8])
        self.call_from_thread(self._add_task_row, key, entry.name)
        try:
            if entry.is_dir:
                outcomes = ops.pull_tree(
                    self.config,
                    self.toolset,
                    entry,
                    target_dir,
                    remote_dir,
                    on_progress=lambda progress: self.call_from_thread(
                        self._on_progress, key, progress
                    ),
                    on_step=lambda message: self.call_from_thread(self._log, message),
                    cancel=token,
                )
                self.call_from_thread(
                    self._log,
                    f"{entry.name} 完成：{len(outcomes)} 个文件 → {target_dir / entry.name}",
                )
            else:
                outcome = ops.pull(
                    self.config,
                    self.toolset,
                    entry,
                    target_dir,
                    remote_dir,
                    on_progress=lambda progress: self.call_from_thread(
                        self._on_progress, key, progress
                    ),
                    on_step=lambda message: self.call_from_thread(self._log, message),
                    cancel=token,
                )
                suffix = "sha256 已校验" if outcome.sha256_verified else "只核对了字节数"
                self.call_from_thread(
                    self._log, f"完成：{outcome.path}（{human_size(outcome.size)}，{suffix}）"
                )
            self.call_from_thread(
                self._remember_transfer,
                history.KIND_DOWNLOAD,
                str(target_dir),
                remote_dir,
                entry.name,
            )
        except Cancelled:
            self.call_from_thread(self._log, f"{entry.name} 已中断")
        except WebSyncError as exc:
            self.call_from_thread(self._log, f"{entry.name} 失败：{exc.message}")
            for problem in exc.problems:
                self.call_from_thread(self._log, f"  {problem}")
        finally:
            self._finish_worker()
        self.call_from_thread(self.refresh_local)

    # ---------------- 磁盘条 ----------------

    @work(thread=True, exclusive=True, group="disks")
    def _refresh_disks(self) -> None:
        """磁盘枚举可能卡在失效的网络挂载上，所以走线程，不占主线程。"""
        try:
            rows = disks.collect()
        except Exception as exc:  # noqa: BLE001 - 取不到就显示原因，不下沉成静默
            self.call_from_thread(self._apply_disks, [], str(exc))
            return
        self.call_from_thread(self._apply_disks, rows, "")

    def _apply_disks(self, rows: list[disks.DiskUsage], error: str) -> None:
        if error:
            markup = f"[red]磁盘信息不可用：{escape(error)}[/]"
        else:
            markup = "\n".join(f"[{style}]{escape(text)}[/]" for style, text in disks.render(rows))
        self.query_one("#disk-bar", Static).update(markup)


def run(config: Config) -> None:
    WebSyncApp(config).run()
