"""配置加载与校验。"""

from __future__ import annotations

from pathlib import Path

import pytest

from websync import config as config_module
from websync.errors import KIND_CONFIG, WebSyncError


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


MINIMAL = """
[remote]
dir = "/远端/db"

[local]
db_dir = ""
download_dir = "/tmp/下载"
"""


def test_missing_config_points_at_init(tmp_path: Path) -> None:
    with pytest.raises(WebSyncError) as caught:
        config_module.load(tmp_path / "absent.toml")

    assert caught.value.kind == KIND_CONFIG
    assert "websync init" in caught.value.problems[0]


def test_broken_toml_is_reported_as_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path / "bad.toml", "[remote\ndir = 1")

    with pytest.raises(WebSyncError) as caught:
        config_module.load(path)

    assert caught.value.kind == KIND_CONFIG


def test_db_dir_may_be_empty_for_pull_only_machine(tmp_path: Path) -> None:
    loaded = config_module.load(_write(tmp_path / "c.toml", MINIMAL))

    assert loaded.db_dir is None
    assert loaded.upload_files() == []


def test_upload_files_skips_sqlite_temp_files(tmp_path: Path) -> None:
    """目录里有什么就列什么，但 -wal / -shm / .part 不是用户要传的东西。"""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    for name in ("novel.db", "system.db", "novel.db-wal", "novel.db-shm", "x.db.part"):
        (db_dir / name).write_bytes(b"x")
    text = MINIMAL.replace('db_dir = ""', f'db_dir = "{db_dir}"')
    loaded = config_module.load(_write(tmp_path / "c.toml", text))

    assert sorted(entry.name for entry in loaded.upload_files()) == ["novel.db", "system.db"]


def test_all_problems_are_collected_at_once(tmp_path: Path) -> None:
    text = """
[remote]
dir = "没有前导斜杠"

[local]
db_dir = ""
download_dir = ""
"""
    with pytest.raises(WebSyncError) as caught:
        config_module.load(_write(tmp_path / "c.toml", text))

    joined = "\n".join(caught.value.problems)
    assert "必须以 / 开头" in joined
    assert "download_dir 不能为空" in joined


def test_registration_block_is_rejected_with_upgrade_hint(tmp_path: Path) -> None:
    """老配置里的 [databases] 现在是多余的，要报出来而不是静默忽略。"""
    text = MINIMAL + '\n[databases]\nnovel = "novel.db"\n'

    with pytest.raises(WebSyncError) as caught:
        config_module.load(_write(tmp_path / "c.toml", text))

    assert any("已取消" in problem for problem in caught.value.problems)


def test_relative_db_dir_is_resolved_once(tmp_path: Path) -> None:
    """导出时子进程 cwd 会切到暂存目录，相对路径必须在这里钉死。"""
    text = MINIMAL.replace('db_dir = ""', 'db_dir = "db"')

    loaded = config_module.load(_write(tmp_path / "c.toml", text))

    assert loaded.db_dir is not None
    assert loaded.db_dir.is_absolute()


def test_default_config_path_lives_inside_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "app"
    home.mkdir()
    monkeypatch.setenv("WEBSYNC_HOME", str(home))

    assert config_module.config_path() == home / "config" / "config.toml"


def test_relative_paths_follow_project_root_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整个项目要能复制走。相对路径必须钉在项目根，不能跟着 cwd 漂。"""
    home = tmp_path / "app"
    (home / "config").mkdir(parents=True)
    cfg_file = home / "config" / "config.toml"
    cfg_file.write_text(MINIMAL.replace('db_dir = ""', 'db_dir = "db"'), encoding="utf-8")
    monkeypatch.setenv("WEBSYNC_HOME", str(home))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    loaded = config_module.load(cfg_file)

    assert loaded.db_dir == (home / "db").resolve()


def test_login_state_is_resolved_inside_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登录态跟着项目走：拷到别的机器，路径照样指得准。"""
    home = tmp_path / "app"
    (home / "config").mkdir(parents=True)
    cfg_file = home / "config" / "config.toml"
    cfg_file.write_text(MINIMAL + '\n[tools]\ngoquark_config = "config/goquark.json"\n', encoding="utf-8")
    monkeypatch.setenv("WEBSYNC_HOME", str(home))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    loaded = config_module.load(cfg_file)

    assert loaded.goquark_config == (home / "config" / "goquark.json").resolve()


def test_write_template_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    config_module.write_template(path)

    with pytest.raises(WebSyncError):
        config_module.write_template(path)

    config_module.write_template(path, force=True)
    assert "goquark_config" in path.read_text(encoding="utf-8")
