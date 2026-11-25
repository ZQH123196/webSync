"""测试夹具。

一律离线：kuake 与 goquark 用 test/fakes 下的替身，只读写临时目录。
sqlite3 用真实的，但只碰临时文件——这是唯一能验证 ``.backup`` 的办法。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3 as sqlite3_module
from dataclasses import dataclass
from pathlib import Path

import pytest

from websync import config as config_module

FAKES = Path(__file__).parent / "fakes"


@dataclass
class Sandbox:
    root: Path
    remote: Path
    db_dir: Path
    home: Path

    def build(
        self,
        *,
        db_dir: Path | None = None,
        inbox: Path | None = None,
        remote_dir: str | None = None,
    ) -> config_module.Config:
        return write_config(
            path=self.root / "config.toml",
            remote_dir=remote_dir or "/",
            db_dir=db_dir if db_dir is not None else self.db_dir,
            inbox=inbox or (self.root / "inbox"),
            goquark_config=self.home / ".config/goquark/config.json",
        )


def write_config(
    *,
    path: Path,
    remote_dir: str,
    db_dir: Path | str | None,
    inbox: Path,
    goquark_config: Path,
) -> config_module.Config:
    lines = [
        "[remote]",
        f'dir = "{remote_dir}"',
        "",
        "[local]",
        f'db_dir = "{db_dir or ""}"',
        f'download_dir = "{inbox}"',
        "",
        "[tools]",
        'kuake_bin = ""',
        'goquark_bin = ""',
        'sqlite3_bin = "sqlite3"',
        f'goquark_config = "{goquark_config}"',
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return config_module.load(path)


def make_database(path: Path, rows: int = 200) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3_module.connect(path)
    try:
        connection.execute("create table chapter(id integer primary key, body text)")
        connection.executemany(
            "insert into chapter(body) values (?)",
            [(f"正文 {index} " + "字" * 40,) for index in range(rows)],
        )
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Sandbox:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("kuake", "goquark"):
        target = bin_dir / name
        shutil.copyfile(FAKES / f"{name}.py", target)
        target.chmod(0o755)

    remote = tmp_path / "remote"
    remote.mkdir()
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    make_database(db_dir / "novel.db")
    make_database(db_dir / "system.db", rows=5)

    home = tmp_path / "home"
    auth = home / ".config/goquark/config.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(
        json.dumps({"session": {"cookies": {"__pus": "pus-in-test", "__puus": "puus-in-test"}}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_KUAKE_ROOT", str(remote))
    monkeypatch.setenv("FAKE_GOQUARK_ROOT", str(remote))
    project = tmp_path / "app"
    project.mkdir()
    monkeypatch.setenv("WEBSYNC_HOME", str(project))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("KUAKE_COOKIE", raising=False)
    monkeypatch.delenv("KUAKE_PUS", raising=False)
    monkeypatch.delenv("KUAKE_PUUS", raising=False)
    return Sandbox(root=tmp_path, remote=remote, db_dir=db_dir, home=home)


@pytest.fixture
def config(sandbox: Sandbox) -> config_module.Config:
    return sandbox.build()
