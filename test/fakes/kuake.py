#!/usr/bin/env python3
"""假 kuake。只做 list / create / upload，状态放在 FAKE_KUAKE_ROOT 目录里。

远端路径映射成 FAKE_KUAKE_ROOT 下的相对路径，所以目录结构是真的。
上传成功时打印与真 kuake 同形状的信封：success / code / data.size。

开关：
  FAKE_KUAKE_FAIL_UPLOAD=1     上传直接失败（退出码 1）
  FAKE_KUAKE_FAIL_CREATE=1     建目录直接失败（退出码 1）
  FAKE_KUAKE_MODE=skipped      上传返回 success:true + SKIPPED，不写文件
  FAKE_KUAKE_TRUNCATE=<字节>   上传后只保留这么多字节，模拟远端大小不符
  FAKE_KUAKE_LIST_HIDES=<名字> list 永远不报这个名字
  FAKE_KUAKE_LIST_FAIL=1       list 输出半截后以退出码 1 结束
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path


def root() -> Path:
    return Path(os.environ["FAKE_KUAKE_ROOT"])


def resolve(remote_path: str) -> Path:
    """远端绝对路径 → 沙箱里的真实路径。"""
    relative = remote_path.strip("/")
    return root() / relative if relative else root()


def hidden_names() -> set[str]:
    permanent = os.environ.get("FAKE_KUAKE_LIST_HIDES", "")
    return {permanent} if permanent else set()


def matches(name: str, patterns: set[str]) -> bool:
    import fnmatch

    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def do_list(remote_path: str) -> int:
    directory = resolve(remote_path)
    if not directory.is_dir():
        print(json.dumps({"success": False, "code": "NOT_FOUND", "message": "目录不存在"}))
        return 1
    patterns = hidden_names()
    entries = [
        entry
        for entry in sorted(directory.iterdir())
        if not entry.name.startswith(".") and not matches(entry.name, patterns)
    ]
    if os.environ.get("FAKE_KUAKE_LIST_FAIL"):
        for entry in entries[:1]:
            print(json.dumps({"file_name": entry.name, "size": entry.stat().st_size}), flush=True)
        print("list failed: 网络错误", file=sys.stderr)
        return 1
    for entry in entries:
        is_dir = entry.is_dir()
        print(
            json.dumps(
                {
                    "fid": f"fid-{entry.name}",
                    "file_name": entry.name,
                    "size": 0 if is_dir else entry.stat().st_size,
                    "dir": is_dir,
                    "mtime": int(entry.stat().st_mtime),
                },
                ensure_ascii=False,
            )
        )
    return 0


def do_create(name: str, parent: str) -> int:
    if os.environ.get("FAKE_KUAKE_FAIL_CREATE"):
        print("create failed: 网络错误", file=sys.stderr)
        return 1
    target = resolve(parent) / name
    target.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"success": True, "code": "OK", "data": {"fid": f"fid-{name}"}}))
    return 0


def do_upload(local: str, remote: str) -> int:
    if os.environ.get("FAKE_KUAKE_FAIL_UPLOAD"):
        print("upload failed: 网络错误", file=sys.stderr)
        return 1
    target = resolve(remote)
    if os.environ.get("FAKE_KUAKE_MODE") == "skipped":
        sys.stderr.write("上传进度: 100% | 速度: 0.00 MB/s | 剩余: 0s\r")
        print(
            json.dumps(
                {"success": True, "code": "SKIPPED", "message": "文件已存在，跳过上传"},
                ensure_ascii=False,
            )
        )
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(local, target)
    truncate = os.environ.get("FAKE_KUAKE_TRUNCATE")
    if truncate:
        target.write_bytes(target.read_bytes()[: int(truncate)])
    for percent in (32, 100):
        sys.stderr.write(f"上传进度: {percent}% | 速度: 4.80 MB/s | 剩余: 3m00s\r")
    sys.stderr.flush()
    print(
        json.dumps(
            {"success": True, "code": "OK", "data": {"size": target.stat().st_size}},
            ensure_ascii=False,
        )
    )
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: kuake <list|create|upload>", file=sys.stderr)
        return 2
    command = argv[0]
    if command == "list":
        return do_list(argv[1] if len(argv) > 1 else "/")
    if command == "create":
        if len(argv) < 3:
            print("usage: kuake create <name> <parent>", file=sys.stderr)
            return 2
        return do_create(argv[1], argv[2])
    if command == "upload":
        if len(argv) < 3:
            print("usage: kuake upload <local> <remote>", file=sys.stderr)
            return 2
        return do_upload(argv[1], argv[2])
    if command == "version":
        print("kuake v1.5.0")
        return 0
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
