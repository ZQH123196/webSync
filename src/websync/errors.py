"""错误类型。

分类用显式字段 ``kind`` 承载，不靠异常继承层级。
``kind`` 同时决定进程退出码：配置错误 2，其余 1。
"""

from __future__ import annotations

from collections.abc import Sequence

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIG = 2

KIND_CONFIG = "config"
KIND_TOOL = "tool"
KIND_TRANSFER = "transfer"
KIND_VERIFY = "verify"
KIND_CANCELLED = "cancelled"


class WebSyncError(Exception):
    """可预期的失败。调用方打印 ``message`` 后按 ``kind`` 退出。"""

    def __init__(self, kind: str, message: str, *, problems: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.problems = tuple(problems)

    @property
    def exit_code(self) -> int:
        return EXIT_CONFIG if self.kind == KIND_CONFIG else EXIT_FAILURE


class Cancelled(WebSyncError):
    """用户主动中断。"""

    def __init__(self, message: str = "已取消") -> None:
        super().__init__(KIND_CANCELLED, message)
