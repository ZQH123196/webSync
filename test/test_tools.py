"""二进制探测。重点是带版本号的文件名。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from websync import tools


@pytest.fixture
def versioned_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """造一个只有带版本号文件名的目录，模拟 /usr/bin/kuake-v1.5.0-linux-amd64。"""
    binary = tmp_path / "bin"
    binary.mkdir()
    for name in ("kuake-v1.5.0-linux-amd64", "goquark-1.2.0-linux-amd64"):
        target = binary / name
        target.write_text("#!/bin/sh\necho 1.5.0\n", encoding="utf-8")
        target.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}/usr/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WEBSYNC_HOME", str(tmp_path / "app"))
    return binary


def test_finds_versioned_kuake(versioned_dir: Path) -> None:
    resolved = tools.resolve(tools.KUAKE)

    assert resolved is not None
    assert resolved.name == "kuake-v1.5.0-linux-amd64"


def test_prefers_highest_version(versioned_dir: Path) -> None:
    newer = versioned_dir / "kuake-v1.10.0-linux-amd64"
    shutil.copyfile(versioned_dir / "kuake-v1.5.0-linux-amd64", newer)
    newer.chmod(0o755)

    resolved = tools.resolve(tools.KUAKE)

    assert resolved is not None
    assert resolved.name == "kuake-v1.10.0-linux-amd64"


def test_bare_explicit_name_is_looked_up_in_path(versioned_dir: Path) -> None:
    # 配置里的默认值就是裸名 "sqlite3"，不能当成相对路径处理
    resolved = tools.resolve(tools.SQLITE3, "sqlite3")

    assert resolved is not None
    assert resolved.is_absolute()


def test_explicit_absolute_path_wins(versioned_dir: Path) -> None:
    chosen = versioned_dir / "goquark-1.2.0-linux-amd64"

    resolved = tools.resolve(tools.GOQUARK, str(chosen))

    assert resolved == chosen


def test_missing_tool_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WEBSYNC_HOME", str(tmp_path / "app"))

    assert tools.resolve("nosuchtool") is None


def test_project_bin_wins_over_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """install-tools.sh 装在项目 bin 里，不该被 PATH 里的同名旧副本顶掉。"""
    project = tmp_path / "app"
    bundled = project / "bin" / "kuake"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#!/bin/sh\necho bundled\n", encoding="utf-8")
    bundled.chmod(0o755)

    foreign_dir = tmp_path / "foreign"
    foreign_dir.mkdir()
    foreign = foreign_dir / "kuake"
    foreign.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
    foreign.chmod(0o755)

    monkeypatch.setenv("WEBSYNC_HOME", str(project))
    monkeypatch.setenv("PATH", str(foreign_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert tools.resolve(tools.KUAKE) == bundled
