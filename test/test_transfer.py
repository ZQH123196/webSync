"""进度行解析与子进程读取。"""

from __future__ import annotations

import sys

import pytest

from websync.transfer import Progress, parse_progress, run_command


@pytest.mark.parametrize(
    ("line", "percent"),
    [
        ("上传进度: 37% | 速度: 3.90 MB/s | 剩余: 4m19s", 37.0),
        ("上传进度: 100% | 速度: 17.82 MB/s | 剩余: 0s", 100.0),
        ("上传进度: 1% | 速度: 1.93 MB/s | 剩余: 13m57s", 1.0),
    ],
)
def test_parse_kuake_progress(line: str, percent: float) -> None:
    parsed = parse_progress(line)

    assert parsed is not None
    assert parsed.percent == percent


def test_parse_kuake_progress_keeps_speed_and_eta() -> None:
    parsed = parse_progress("上传进度: 37% | 速度: 3.90 MB/s | 剩余: 4m19s")

    assert parsed == Progress(percent=37.0, speed="3.90 MB/s", eta="4m19s")


def test_parse_goquark_progress() -> None:
    parsed = parse_progress("progress 71.0%  44.14 MiB/s")

    assert parsed is not None
    assert parsed.percent == 71.0
    assert parsed.speed == "44.14 MiB/s"


@pytest.mark.parametrize("line", ["", "OK path=/tmp/a size=1", "下载失败: 超过文件下载大小限制"])
def test_parse_progress_ignores_unrelated_lines(line: str) -> None:
    assert parse_progress(line) is None


def test_run_command_splits_progress_on_carriage_return() -> None:
    seen: list[Progress] = []
    script = (
        "import sys;"
        "sys.stderr.write('progress 10.0%  1.00 MiB/s\\rprogress 50.0%  2.00 MiB/s\\r');"
        "sys.stdout.write('done');"
    )

    result = run_command([sys.executable, "-c", script], on_progress=seen.append)

    assert result.returncode == 0
    assert result.stdout.strip() == "done"
    assert [item.percent for item in seen] == [10.0, 50.0]


# 真 kuake v1.5.0 上传成功后 stdout 的实际形状（2026-10-29 实测捕获）
REAL_OK_ENVELOPE = (
    '{"success": true, "code": "OK", "message": "上传完成（秒传）", "data": '
    '{"fid": "7a7df7618e3847bf80854ce35c0eee87", "finish": true, "md5": "5a5ede98", '
    '"obj_key": "09a14e90", "sha1": "ef91cb51", "size": 1713913856, "task_id": "96489ce1"}}'
)

REAL_SKIPPED_ENVELOPE = (
    '{"success": true, "code": "SKIPPED", "message": "文件已存在，跳过上传"}'
)


def test_real_ok_envelope_passes_verification() -> None:
    from websync.ops import require_upload_ok

    require_upload_ok(REAL_OK_ENVELOPE, 1713913856, "20261029100147-novel.db")


def test_real_ok_envelope_with_wrong_size_is_rejected() -> None:
    from websync.errors import WebSyncError
    from websync.ops import require_upload_ok

    with pytest.raises(WebSyncError):
        require_upload_ok(REAL_OK_ENVELOPE, 1, "20261029100147-novel.db")


def test_real_skipped_envelope_is_rejected_despite_success_true() -> None:
    from websync.errors import KIND_VERIFY, WebSyncError
    from websync.ops import require_upload_ok

    with pytest.raises(WebSyncError) as caught:
        require_upload_ok(REAL_SKIPPED_ENVELOPE, 11108352, "20261029100147-system.db")

    assert caught.value.kind == KIND_VERIFY


def test_run_command_reports_missing_binary_as_websync_error() -> None:
    from websync.errors import WebSyncError

    with pytest.raises(WebSyncError):
        run_command(["/nonexistent/binary-xyz"])
