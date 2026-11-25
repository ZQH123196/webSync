#!/usr/bin/env python3
"""假 goquark。只做 download。

失败开关：
  FAKE_GOQUARK_NO_OK=1     不打印 OK 行（真 goquark 失败时退出码仍是 0）
  FAKE_GOQUARK_TRUNCATE=1  只写出前 100 字节
"""

import os
import sys
from pathlib import Path


def root() -> Path:
    return Path(os.environ["FAKE_GOQUARK_ROOT"])


def do_download(remote: str, local: str) -> int:
    relative = remote.strip("/")
    source = root() / relative if relative else root()
    if not source.is_file():
        print(f"error: path not found: {remote}", file=sys.stderr)
        return 0
    data = source.read_bytes()
    if os.environ.get("FAKE_GOQUARK_TRUNCATE"):
        data = data[:100]
    Path(local).write_bytes(data)
    sys.stderr.write("progress 55.0%  44.00 MiB/s\rprogress 100.0%  44.00 MiB/s\r")
    sys.stderr.flush()
    if not os.environ.get("FAKE_GOQUARK_NO_OK"):
        print(f"OK path={local} size={len(data)} elapsed=1.0s")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "download":
        return do_download(argv[1], argv[2])
    if argv and argv[0] in ("--version", "version"):
        print("goquark version 1.2.0")
        return 0
    print("usage: goquark download <remote> <local>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
