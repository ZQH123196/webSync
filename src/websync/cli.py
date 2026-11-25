"""命令行入口。

不带子命令时进入交互界面；带子命令时只做非交互动作，便于远程排障。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import config as config_module
from . import ops
from .errors import EXIT_OK, WebSyncError
from .naming import human_size

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    help="夸克网盘中转：在多台机器之间传文件，SQLite 库会先做一致快照。",
)


def _load_or_die(path: Optional[Path]) -> config_module.Config:
    try:
        return config_module.load(path)
    except WebSyncError as exc:
        err_console.print(f"[red]{exc.message}")
        for problem in exc.problems:
            err_console.print(f"  - {problem}")
        raise typer.Exit(exc.exit_code) from exc


@app.callback()
def root(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="配置文件路径，默认 <项目>/config/config.toml"
    ),
    version: bool = typer.Option(False, "--version", help="打印版本后退出"),
) -> None:
    if version:
        from . import __version__

        console.print(f"websync {__version__}")
        raise typer.Exit(EXIT_OK)
    ctx.obj = {"config": config}
    if ctx.invoked_subcommand is not None:
        return
    loaded = _load_or_die(config)
    from .app import run

    run(loaded)
    raise typer.Exit(EXIT_OK)


@app.command()
def init(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", help="覆盖已存在的配置"),
) -> None:
    """写出配置模板。"""
    target = ctx.obj["config"] if ctx.obj else None
    try:
        written = config_module.write_template(target, force=force)
    except WebSyncError as exc:
        err_console.print(f"[red]{exc.message}")
        for problem in exc.problems:
            err_console.print(f"  - {problem}")
        raise typer.Exit(exc.exit_code) from exc
    console.print(f"已写入 {written}")
    console.print(escape("改好 [remote].dir 与 [local] 两段后再跑 websync doctor。"))


@app.command()
def doctor(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="按行输出 JSON"),
    for_scope: str = typer.Option(
        "all", "--for", help="按用途判定成败：all 全部 / push 只推库 / pull 只拉库"
    ),
) -> None:
    """检查本机环境。一次报完所有问题。"""
    if for_scope not in ("all", "push", "pull"):
        err_console.print(f"[red]--for 只接受 all / push / pull，收到 {for_scope}")
        raise typer.Exit(2)
    loaded = _load_or_die(ctx.obj["config"] if ctx.obj else None)
    checks = ops.doctor(loaded)
    if as_json:
        for check in checks:
            console.print(
                json.dumps(
                    {
                        "name": check.name,
                        "ok": check.ok,
                        "detail": check.detail,
                        "scope": check.scope,
                    },
                    ensure_ascii=False,
                ),
                soft_wrap=True,
                markup=False,
                highlight=False,
            )
    else:
        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("", width=4)
        table.add_column("检查项", width=14)
        # 路径很长，必须折行而不是省略号，否则诊断信息读不全
        table.add_column("详情", overflow="fold")
        for check in checks:
            mark = "[green]OK[/]" if check.ok else "[red]FAIL[/]"
            table.add_row(mark, escape(check.name), escape(check.detail))
        console.print(table)
    def relevant(check: ops.Check) -> bool:
        return for_scope == "all" or check.scope in ("all", for_scope)

    failed = [check for check in checks if not check.ok and relevant(check)]
    if failed:
        err_console.print(f"\n[red]{len(failed)} 项不通过（判定范围 {for_scope}）")
        raise typer.Exit(1)
    console.print(f"\n[green]全部通过（判定范围 {for_scope}）")


@app.command()
def ls(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="按行输出 JSON"),
    keep: int = typer.Option(0, "--keep", help="只显示最近 N 条，0 表示全部"),
) -> None:
    """列出远端目录。目录在前，文件按时间倒序。"""
    loaded = _load_or_die(ctx.obj["config"] if ctx.obj else None)
    toolset = ops.resolve_tools(loaded)
    try:
        found = ops.list_remote(loaded, toolset)
    except WebSyncError as exc:
        err_console.print(f"[red]{exc.message}")
        for problem in exc.problems:
            err_console.print(f"  - {problem}")
        raise typer.Exit(exc.exit_code) from exc

    shown = found[:keep] if keep > 0 else found
    if as_json:
        for item in shown:
            console.print(
                json.dumps(
                    {
                        "name": item.name,
                        "size": item.size,
                        "dir": item.is_dir,
                        "mtime": item.mtime,
                    },
                    ensure_ascii=False,
                ),
                soft_wrap=True,
                markup=False,
                highlight=False,
            )
        return
    if not shown:
        console.print("远端目录是空的")
        return
    for item in shown:
        size = "-" if item.is_dir else human_size(item.size)
        console.print(f"{item.name}  {size:>10}")


def main() -> None:
    try:
        app()
    except WebSyncError as exc:
        err_console.print(f"[red]{exc.message}")
        for problem in exc.problems:
            err_console.print(f"  - {problem}")
        sys.exit(exc.exit_code)


if __name__ == "__main__":
    main()
