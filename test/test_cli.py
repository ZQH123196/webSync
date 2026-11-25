"""命令行入口的退出码与输出形状。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from websync.cli import app

from .conftest import Sandbox

RUNNER = CliRunner()

MAIN = "20260101000000-novel.db"


def _seed(sandbox: Sandbox) -> None:
    target = sandbox.remote / MAIN
    shutil.copyfile(sandbox.db_dir / "novel.db", target)
    (sandbox.remote / f"{MAIN}.sha256").write_text("deadbeef  x\n", encoding="utf-8")


def test_ls_lists_snapshots_newest_first(config, sandbox: Sandbox) -> None:
    _seed(sandbox)
    (sandbox.remote / "20250101000000-system.db").write_bytes(b"old")

    result = RUNNER.invoke(app, ["-c", str(config.path), "ls"])

    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert MAIN in lines[0]
    assert "20250101000000-system.db" in lines[-1]


def test_ls_json_is_line_delimited(config, sandbox: Sandbox) -> None:
    _seed(sandbox)

    result = RUNNER.invoke(app, ["-c", str(config.path), "ls", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output.splitlines()[0])
    assert payload["name"] == MAIN


def test_missing_config_exits_with_two(tmp_path: Path) -> None:
    result = RUNNER.invoke(app, ["-c", str(tmp_path / "absent.toml"), "ls"])

    assert result.exit_code == 2
    assert "websync init" in result.output


def test_doctor_for_pull_ignores_missing_sqlite3(
    config, sandbox: Sandbox, monkeypatch
) -> None:
    """只拉库的机子没有 sqlite3 也不该被判失败。"""
    from websync import tools

    real_detect = tools.detect

    def without_sqlite3(name: str, explicit: str = ""):  # noqa: ANN202
        if name == tools.SQLITE3:
            return None
        return real_detect(name, explicit)

    monkeypatch.setattr(tools, "detect", without_sqlite3)

    result = RUNNER.invoke(app, ["-c", str(config.path), "doctor", "--for", "pull"])

    assert result.exit_code == 0


def test_doctor_rejects_unknown_scope(config) -> None:
    result = RUNNER.invoke(app, ["-c", str(config.path), "doctor", "--for", "sideways"])

    assert result.exit_code == 2


def test_init_refuses_to_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "conf" / "config.toml"
    first = RUNNER.invoke(app, ["-c", str(target), "init"])
    second = RUNNER.invoke(app, ["-c", str(target), "init"])

    assert first.exit_code == 0
    assert second.exit_code == 2
