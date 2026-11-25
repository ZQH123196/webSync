"""推库与拉库的完整行为。外部工具全部是 test/fakes 下的替身。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from websync import ops, remote
from websync.errors import KIND_CONFIG, KIND_TRANSFER, KIND_VERIFY, WebSyncError

from .conftest import Sandbox

PREFIX = "20260101000000"
MAIN = f"{PREFIX}-system.db"


def _toolset(config):  # noqa: ANN001 - 夹具返回类型在 conftest
    return ops.resolve_tools(config)


def _push(config, *, prefix: str = PREFIX, attempts: int = 1):  # noqa: ANN001
    return ops.push(
        config,
        _toolset(config),
        config.db_dir / "system.db",
        prefix=prefix,
        verify_attempts=attempts,
        verify_delay=0,
    )


# ---------------------------------------------------------------- push


def test_push_uploads_snapshot_and_sidecar(config, sandbox: Sandbox) -> None:
    outcome = _push(config)

    uploaded = sandbox.remote / MAIN
    sidecar = sandbox.remote / f"{MAIN}.sha256"

    assert uploaded.is_file()
    assert outcome.name == MAIN
    assert outcome.size == uploaded.stat().st_size
    assert sidecar.read_text(encoding="utf-8").split()[0] == outcome.sha256
    assert outcome.sha256 == ops.sha256_of(uploaded)


def test_push_uploads_any_file_without_snapshot(config, sandbox: Sandbox) -> None:
    """不是 SQLite 就原样上传。用户传什么就传什么。"""
    note = sandbox.db_dir / "报告 2026.txt"
    note.write_text("不是数据库", encoding="utf-8")

    outcome = ops.push(
        config,
        _toolset(config),
        note,
        prefix=PREFIX,
        verify_attempts=1,
        verify_delay=0,
    )

    uploaded = sandbox.remote / f"{PREFIX}-报告 2026.txt"
    assert outcome.name == f"{PREFIX}-报告 2026.txt"
    assert uploaded.read_bytes() == note.read_bytes()


def test_push_cleans_staging_after_success(config, sandbox: Sandbox) -> None:
    _push(config)

    leftovers = list(ops.staging_dir().glob("*")) if ops.staging_dir().is_dir() else []

    assert leftovers == []


def test_push_cleans_staging_after_failure(
    config, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败也不能把 1.6 GB 的临时文件留在盘上。"""
    monkeypatch.setenv("FAKE_KUAKE_FAIL_UPLOAD", "1")

    with pytest.raises(WebSyncError):
        _push(config)

    leftovers = list(ops.staging_dir().glob("*")) if ops.staging_dir().is_dir() else []
    assert leftovers == []


def test_push_refuses_when_remote_already_has_same_name(config, sandbox: Sandbox) -> None:
    (sandbox.remote / MAIN).write_bytes(b"existing")

    with pytest.raises(WebSyncError) as caught:
        _push(config)

    assert caught.value.kind == KIND_VERIFY
    assert "同名" in caught.value.message


def test_push_treats_skipped_as_failure(
    config, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kuake 遇同名返回 success:true + SKIPPED，退出码 0。只看退出码会把静默跳过当成功。"""
    monkeypatch.setenv("FAKE_KUAKE_MODE", "skipped")

    with pytest.raises(WebSyncError) as caught:
        _push(config)

    assert caught.value.kind == KIND_VERIFY
    assert "SKIPPED" in caught.value.message


def test_push_detects_remote_size_mismatch(
    config, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_KUAKE_TRUNCATE", "100")

    with pytest.raises(WebSyncError) as caught:
        _push(config)

    assert caught.value.kind == KIND_VERIFY
    assert "字节数" in caught.value.message


def test_push_reports_upload_failure(config, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_KUAKE_FAIL_UPLOAD", "1")

    with pytest.raises(WebSyncError) as caught:
        _push(config)

    assert "上传" in caught.value.message


def test_push_fails_when_remote_never_lists_file(
    config, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_KUAKE_LIST_HIDES", MAIN)

    with pytest.raises(WebSyncError) as caught:
        _push(config, attempts=2)

    assert caught.value.kind == KIND_VERIFY


# ---------------------------------------------------------------- 远端列表


def test_list_snapshots_rejects_partial_listing(
    config, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """退出码非零时哪怕解析出条目也不能用：少一条会被当成「远端没有」。"""
    (sandbox.remote / MAIN).write_bytes(b"x")
    monkeypatch.setenv("FAKE_KUAKE_LIST_FAIL", "1")

    with pytest.raises(WebSyncError) as caught:
        ops.list_remote(config, _toolset(config))

    assert caught.value.kind == KIND_TRANSFER


def test_wait_for_remote_retries_transient_list_errors(
    config, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def flaky(tool, remote_dir, env=None, cancel=None):  # noqa: ANN001
        calls.append(1)
        if len(calls) < 3:
            raise WebSyncError(KIND_TRANSFER, "临时失败")
        return [remote.RemoteEntry(MAIN, 42)]

    monkeypatch.setattr(remote, "list_dir", flaky)

    size = ops._wait_for_remote(
        config,
        _toolset(config),
        MAIN,
        42,
        config.remote_dir,
        attempts=5,
        first_delay=0,
    )

    assert size == 42
    assert len(calls) == 3


# ---------------------------------------------------------------- pull


def _seed_remote(sandbox: Sandbox, name: str) -> Path:
    target = sandbox.remote / name
    shutil.copyfile(sandbox.db_dir / "system.db", target)
    return target


def test_pull_verifies_sha256(config, sandbox: Sandbox) -> None:
    stored = _seed_remote(sandbox, MAIN)
    digest = ops.sha256_of(stored)
    (sandbox.remote / f"{MAIN}.sha256").write_text(f"{digest}  {MAIN}\n", encoding="utf-8")
    target = remote.RemoteEntry(MAIN, stored.stat().st_size)

    outcome = ops.pull(config, _toolset(config), target, sandbox.root / "inbox")

    assert outcome.sha256_verified is True
    assert outcome.path.is_file()
    assert ops.sha256_of(outcome.path) == digest
    assert not outcome.path.with_name(outcome.path.name + ".part").exists()


def test_pull_without_sidecar_falls_back_to_size_check(config, sandbox: Sandbox) -> None:
    stored = _seed_remote(sandbox, MAIN)
    target = remote.RemoteEntry(MAIN, stored.stat().st_size)

    outcome = ops.pull(config, _toolset(config), target, sandbox.root / "inbox")

    assert outcome.sha256_verified is False
    assert outcome.size == stored.stat().st_size


def test_pull_rejects_when_ok_line_missing(
    config, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """goquark 失败时退出码仍是 0，只能认 OK 行。"""
    stored = _seed_remote(sandbox, MAIN)
    target = remote.RemoteEntry(MAIN, stored.stat().st_size)
    monkeypatch.setenv("FAKE_GOQUARK_NO_OK", "1")

    with pytest.raises(WebSyncError) as caught:
        ops.pull(config, _toolset(config), target, sandbox.root / "inbox")

    assert "下载失败" in caught.value.message


def test_pull_removes_part_file_on_failure(
    config, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = _seed_remote(sandbox, MAIN)
    target = remote.RemoteEntry(MAIN, stored.stat().st_size)
    monkeypatch.setenv("FAKE_GOQUARK_TRUNCATE", "1")
    inbox = sandbox.root / "inbox"

    with pytest.raises(WebSyncError):
        ops.pull(config, _toolset(config), target, inbox)

    assert list(inbox.glob("*.part")) == []
    assert list(inbox.iterdir()) == []


def test_pull_rejects_truncated_file(
    config, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = _seed_remote(sandbox, MAIN)
    target = remote.RemoteEntry(MAIN, stored.stat().st_size)
    monkeypatch.setenv("FAKE_GOQUARK_TRUNCATE", "1")

    with pytest.raises(WebSyncError) as caught:
        ops.pull(config, _toolset(config), target, sandbox.root / "inbox")

    assert caught.value.kind == KIND_VERIFY


def test_pull_rejects_sha256_mismatch(config, sandbox: Sandbox) -> None:
    stored = _seed_remote(sandbox, MAIN)
    (sandbox.remote / f"{MAIN}.sha256").write_text("deadbeef  x\n", encoding="utf-8")
    target = remote.RemoteEntry(MAIN, stored.stat().st_size)

    with pytest.raises(WebSyncError) as caught:
        ops.pull(config, _toolset(config), target, sandbox.root / "inbox")

    assert "sha256" in caught.value.message


# ---------------------------------------------------------------- 凭证与体检


def test_goquark_cookies_ignores_half_pair(config, sandbox: Sandbox) -> None:
    config.goquark_config.write_text(
        '{"session": {"cookies": {"__pus": "only-pus"}}}', encoding="utf-8"
    )

    assert ops.goquark_cookies(config) == {}


def test_goquark_cookies_survives_broken_shape(config, sandbox: Sandbox) -> None:
    config.goquark_config.write_text('{"session": "不是对象"}', encoding="utf-8")

    assert ops.goquark_cookies(config) == {}


def test_kuake_env_refuses_without_credentials(config, sandbox: Sandbox) -> None:
    """登录态在用户主目录，换机器最容易漏。缺了就报出缺哪个文件。"""
    config.goquark_config.unlink()

    with pytest.raises(WebSyncError) as caught:
        ops.kuake_env(config)

    assert caught.value.kind == KIND_CONFIG
    assert str(config.goquark_config) in " ".join(caught.value.problems)


def test_doctor_passes_in_sandbox(config) -> None:
    checks = ops.doctor(config, _toolset(config))

    failed = [check for check in checks if not check.ok]
    assert failed == []


def test_doctor_flags_missing_kuake(config, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = sandbox.root / "empty-bin"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setenv("HOME", str(sandbox.home))

    checks = ops.doctor(config)

    kuake = next(check for check in checks if check.name == "kuake")
    assert kuake.ok is False
    # 远端目录这一项也不能假装通过
    remote_check = next(check for check in checks if check.name == "远端目录")
    assert remote_check.ok is False


# ---------------------------------------------------------------- 目录


def test_push_tree_keeps_relative_structure(config, sandbox: Sandbox) -> None:
    folder = sandbox.db_dir / "备份"
    (folder / "子目录").mkdir(parents=True)
    (folder / "a.txt").write_text("A", encoding="utf-8")
    (folder / "子目录" / "b.txt").write_text("B", encoding="utf-8")

    outcomes = ops.push_tree(
        config, _toolset(config), folder, prefix=PREFIX, verify_attempts=1, verify_delay=0
    )

    root = sandbox.remote / f"{PREFIX}-备份"
    assert (root / "a.txt").read_text(encoding="utf-8") == "A"
    assert (root / "子目录" / "b.txt").read_text(encoding="utf-8") == "B"
    assert len(outcomes) == 2


def test_pull_tree_rebuilds_directories(config, sandbox: Sandbox) -> None:
    remote_root = sandbox.remote / "旧备份"
    (remote_root / "子目录").mkdir(parents=True)
    (remote_root / "a.txt").write_text("A", encoding="utf-8")
    (remote_root / "子目录" / "b.txt").write_text("B", encoding="utf-8")
    destination = sandbox.root / "inbox"

    outcomes = ops.pull_tree(
        config, _toolset(config), remote.RemoteEntry("旧备份", 0, is_dir=True), destination
    )

    assert (destination / "旧备份" / "a.txt").read_text(encoding="utf-8") == "A"
    assert (destination / "旧备份" / "子目录" / "b.txt").read_text(encoding="utf-8") == "B"
    assert len(outcomes) == 2
