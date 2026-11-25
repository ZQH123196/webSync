"""说明文案：路径要取当前配置，用户照着按就能走完。"""

from __future__ import annotations

from dataclasses import replace

from websync.guide import build_help_text

from .conftest import Sandbox


def test_help_text_uses_configured_paths(config, sandbox: Sandbox) -> None:
    text = build_help_text(config)

    assert str(sandbox.db_dir) in text
    assert config.remote_dir in text
    assert "推库（本机 → 网盘）" in text
    assert "拉库（网盘 → 本机）" in text
    assert "按 g，填上面的上传目录。" in text


def test_help_text_survives_missing_upload_dir(config) -> None:
    """只拉库的机器没配 db_dir，说明仍要能打开。"""
    text = build_help_text(replace(config, db_dir=None))

    assert "未配置" in text
    assert "拉库（网盘 → 本机）" in text
