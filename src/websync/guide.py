"""推库与拉库的操作说明。

单独成模块的理由：TUI 的 ``?`` 与 ``websync steps`` 讲同一份话，
而且路径要取当前配置。渲染层只负责摆位置，不改文案。
"""

from __future__ import annotations

from .config import Config

STEPS_TEMPLATE = """\
这台机器的操作步骤

上传目录（[local].db_dir）
{upload_dir}
远端目录（[remote].dir）
{remote_dir}

推库（本机 → 网盘）
1. ./start_webSync.sh
2. 按 2 切到本地窗口。
3. 按 g，填上面的上传目录。
4. 光标移到 novel.db，按 u。
5. 按 y 或回车，确认导出并上传。
6. 看到「快照通过完整性检查」与「完成」，这一份就传完了。
7. 要传 system.db 时，重复第 4 步。

拉库（网盘 → 本机）
1. ./start_webSync.sh
2. 按 1 切到远端窗口。
3. 光标移到 20261031112851-novel.db，按 d。
4. 落到本地当前目录。按 h 可以重做上一次传输。

落地到 trans-app
1. 停掉 trans-app。
2. 把 20261031112851-novel.db 改名为 novel.db，替换
   apps/server/db/database/ 下的同名文件。
3. 启动 trans-app。
"""

HELP_TEXT = """\
WebSync 说明

导出 db 没有单独命令。选中 SQLite 文件按 u，上传流程自动导出快照。
WebSync 替你执行 sqlite3 .backup，不用另跑命令，也不用停 trans-app。

SQLite 文件的上传流程
1. sqlite3 .backup 导出一致快照。直接复制会拿到撕裂副本，所以不复制。
2. 快照跑 PRAGMA integrity_check。不通过就中止，不传坏库。
3. 远端名 = 北京时间前缀 + 原文件名，例如 20261031112851-novel.db。
4. 主文件旁附 .sha256，供拉库的机器校验内容。
5. 上传后核对远端字节数。远端已有同名文件直接拒绝，不覆盖。

哪些文件走快照
文件头前 16 字节是 SQLite format 3 的文件才做快照，不看扩展名。
所以 novel.db 与 novel.db.bak-* 都走快照，.txt 这类文件原样上传。

按键
1 远端  2 本地  u 上传  d 下载  Enter 进入  Backspace 上级
g 跳转  h 历史  r 刷新  ? 说明  Esc 中断  q 退出

下载
1. 按 1 切到远端窗口，选中文件按 d。
2. 有 .sha256 就校验内容，没有只核对字节数。

注意
- 别传 novel.db.bak-* 这类旧备份，会白占一次快照与上传。
- .db-wal 与 .db-shm 不显示在列表里，不要手工传。
- 上传中按 Esc 中断。按 q 退出会先杀掉上传进程。
"""


def build_help_text(config: Config) -> str:
    """拼出这台机器的说明。路径取当前配置，用户照着按就行。"""
    upload = str(config.db_dir) if config.db_dir else "未配置（只拉库的机器不需要）"
    steps = STEPS_TEMPLATE.format(upload_dir=upload, remote_dir=config.remote_dir)
    return f"{steps}\n{HELP_TEXT}"
