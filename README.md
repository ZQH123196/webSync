# WebSync

夸克网盘中转：在多台机器之间传文件。SQLite 库会先做一致快照，其他文件原样上传。

## 为什么是独立工具

trans-app 并不部署在所有机器上。有些机器只跑 sglang-omni，有些只跑 alignServer。
WebSync 因此不挂在 WebUI 里，也不读 trans-app 的配置。
它只需要三个外部可执行文件，任何一台机器都能单独跑。

两个上游工具分工固定，不要互换：

| 工具 | 用途 | 实测速度 |
|---|---|---|
| `kuake` | 上传 | 1.6 GB 用 356 s，约 4.80 MB/s，已接近客户端天花板 |
| `goquark` | 下载 | 1.6 GB 用 40–175 s，分块并发，20–45 MB/s |

`kuake` 不能下一个 1.6 GB 的文件：它拒绝大于 144 MiB 的下载。
所以拉库只能走 `goquark`。

## 依赖

1. `uv`
2. `curl`，只有装工具时用得上
3. `sqlite3`，只有推库的机器需要

两个外部工具由 `install-tools.sh` 装到项目里，不用自己找：

| 工具 | 用途 | 来源 |
|---|---|---|
| `kuake` | 上传，以及列远端目录 | `zhangjingwei/kuake_cli` |
| `goquark` | 取回文件 | `ButterFuture/GoQuark` |

`kuake` 不只用于上传：列远端目录也靠它。所以**拉库的机器同样需要 `kuake`**，
`goquark` 只负责取文件。

两个工具都是非官方客户端，AGPL-3.0。`kuake` 上游不发布校验文件，
只能确认下载成功；`goquark` 会比对上游的 `SHA256SUMS`，不匹配就拒绝安装。

## 安装

只有两个命令，装完就启动：

```bash
cd apps/supportProject/webSync
./install.sh            # 装 uv（缺才装）、Python 环境、kuake、goquark
./start_webSync.sh      # 进交互界面
```

`install.sh` 一条龙做完五件事：

1. 没有 `uv` 就装（官方脚本，落 `~/.local/bin`，不需要 sudo）。
2. `uv sync --frozen` 建 `.venv`。启动时因此不用联网装依赖。
3. 把 `kuake` 与 `goquark` 装进 `<项目>/bin/`，随项目复制走，
   不碰 `~/.local/bin`，也不用改 PATH。
4. 把 goquark 登录态收进 `config/goquark.json`。项目里已有就不动。
5. 自检三个版本号。

**已经装好的不会重复下载。** `bin/` 里存在非空的 `kuake` / `goquark`
就直接跳过，连版本号都不查。裸名与带版本号的名字
（`kuake-v1.5.0-linux-amd64` 这种）都认。

可执行位丢了会自动补上。压缩包、网盘、U 盘拷贝经常把它丢掉，
文件本身是好的。只有空文件才当作没装。

**离线也能装。** 把一份带 `bin/` 的目录拷过去，`./install.sh` 会跳过下载，
只剩 uv 装 Python 依赖那一步。GitHub 取不到版本号时会在 20 秒内失败，
给出「拷 bin」与「手工下载」两条路，不会挂住。

要重装外部工具加 `--force`：

```bash
./install.sh --force
INSTALL_DIR=/opt/bin ./install.sh   # 装到别处，此时需要它在 PATH 里
```

**网络。** 本机若有 `/etc/network_turbo`，脚本会在下载前加载它，
然后清掉 `http_proxy` / `https_proxy`——它设的代理会让 curl 连不上。
文件不存在时什么都不做。

注意 `bin/` 在 gitignore 里。用 `cp -r` 复制目录会带走它，
`git clone` 不会，克隆后要跑一次 `./install.sh`。

没装环境就跑 `start_webSync.sh`，它会直接提示「先跑 ./install.sh」，
不会现场联网装依赖。

## 启动

日常只从启动脚本进：

```bash
./start_webSync.sh              # 进交互界面
./start_webSync.sh doctor       # 带参数时原样透传给 websync 子命令
./start_webSync.sh ls --json
```

脚本做三件事：

1. 把 `~/.local/bin` 补进 PATH。uv 常装在那里，桌面快捷方式与 cron 里往往不在。
2. 没有终端又没带子命令时直接拒绝，不让界面画到一半退出。
3. 用 `uv run --frozen` 启动。`pyproject.toml` 与 `uv.lock` 不一致时立刻失败，
   不会在启动期悄悄升级依赖。

## 配置

配置在项目里：`config/config.toml`。已经带好一份可用的默认值，
换机器一般不用改。

```bash
./start_webSync.sh doctor
```

要改的话直接编辑 `config/config.toml`。想换一份，用
`WEBSYNC_CONFIG=/别的/路径.toml` 或 `-c` 指定。

删掉 `config/config.toml` 后，`init` 会重新写一份模板。

**相对路径一律相对项目根目录**解析，不随调用时的工作目录变化。
所以 `db_dir = "../../server/db/database"` 在哪个目录启动都指向同一处。

`init` 会把模板写到 `config/config.toml`，已存在时拒绝覆盖：

```bash
./start_webSync.sh init           # 已存在会失败
./start_webSync.sh init --force   # 覆盖
```

配置只有三项要改：

| 键 | 含义 |
|---|---|
| `[remote].dir` | 夸克网盘上的中转目录 |
| `[local].db_dir` | 上传目录，这个目录里的文件都会列出来。只有推库的机器要填 |
| `[local].download_dir` | 下载目录，默认 `~/download`，本地窗口一打开就在这里 |

**上传不用登记文件。** 本地窗口里看到什么就能传什么。
按 `g` 填路径，可以去任何目录。SQLite 库会先做一致快照，其他文件原样上传。

## 目录布局

配置跟着项目走，缓存不跟。复制整个目录就能用：配置在里面，
缓存第一次运行自己建。

```text
webSync/
├── config/config.toml   配置，随项目复制
├── config/goquark.json  登录态，随项目复制，不入 git
├── bin/                 kuake 与 goquark，由 install.sh 装
└── .venv/               uv 建的解释器环境
```

`bin/` 与 `config/goquark.json` 在 gitignore 里。前者由安装脚本重建，
后者含密码，两者都不进仓库。

缓存与暂存在项目之外：

```text
~/.cache/websync/
├── staging/   导出快照的临时落地处，上传完就删
├── logs/      命令日志
└── goquark/   goquark 的可写配置副本
```

这些都是可丢弃的中间产物。删掉没有副作用，下次运行自动重建。
放项目里只会让副本带上垃圾。

凭证不写进配置。`kuake` 需要 `__pus` 与 `__puus` 两个 cookie，
WebSync 优先读环境变量 `KUAKE_COOKIE` 或 `KUAKE_PUS`/`KUAKE_PUUS`；
两者都没有时，复用 goquark 登录态里同一对 cookie。

**登录态在项目里：`config/goquark.json`。** 整包复制到别的机器就能用，
不用再登录一次。`install.sh` 发现它不存在时，会从
`~/.config/goquark/config.json` 复制一份进来。

这个文件含会话 cookie，等同密码，已列入 `.gitignore`。缺了它 WebSync 直接报
`kuake 没有凭证` 并列出该路径，不会静默失败。两条路：

1. 在那台机器上跑一次 `goquark login`，再跑 `./install.sh` 收进项目，或
2. 直接把本机的 `config/goquark.json` 拷到目标项目的同一位置，权限 600。

## 交互界面

```bash
uv run websync
```

最上方是本机各磁盘的剩余空间，5 秒刷新一次。同一块盘挂载多处的只显示最短的挂载点，
伪文件系统不显示。剩余低于 30% 变黄，低于 10% 变红。

左边是远端，右边是本地，两边结构一样：名称、时间、大小。
本地窗口一打开落在下载目录（`~/download`）。要传库就走进上传目录：
`g` 填路径，或用 `..` 逐级往上走。
目录排在前面，文件按修改时间倒序，最新的在最上面。
当前操作的窗口边框高亮，标题里写着这个窗口的路径。

| 按键 | 动作 |
|---|---|
| `1` | 切到远端窗口 |
| `2` | 切到本地窗口（下载目录） |
| `Enter` | 进入光标下的目录（或 `..` 回上级） |
| `Backspace` | 回上级目录 |
| `g` | 跳到指定路径，两边都能用 |
| `h` | 历史：最近走过的目录与最近传过的东西 |
| `u` | 把本地选中的文件或文件夹传到远端当前目录 |
| `d` | 把远端选中的文件或文件夹下载到本地当前目录 |
| `r` | 重读当前窗口 |
| `Esc` | 中断正在跑的传输 |
| `q` | 退出 |

上传永远取本地窗口的选中项，下载永远取远端窗口的选中项。
选中目录就整个传，目录结构照搬。

## 历史

按 `h` 打开。两段合成一张表，回车执行：

- **目录**：走过的远端与本地目录，按使用次数排序，回车跳过去。
- **传输**：最近成功传过的东西，回车用同样的源和目的地再做一遍。

记录写在 `~/.cache/websync/history.json`，不属于项目，删掉没有副作用。
文件坏了就当空的，并在传输日志里留一行原因。

远端路径不再是写死的 `[remote].dir`：用 `1` 选中远端窗口后，回车进目录、
`Backspace` 回上级、`g` 直接跳。`[remote].dir` 只是启动时的位置。

## 非交互命令

机器上没有终端或要写脚本时用这三个：

```bash
uv run websync doctor               # 体检，一次报完所有问题
uv run websync doctor --for pull    # 只按拉库判定成败
uv run websync ls                   # 列远端目录
uv run websync ls --json --keep 5   # 最近 5 条，行 JSON
```

`doctor --for pull` 只把拉库必需的检查算进退出码。
只跑 alignServer 的机器没有 `sqlite3` 也不会被判失败。

退出码：`0` 成功，`1` 运行失败，`2` 配置或参数错误。

## 远端文件命名

```text
20261029100147-novel.db
20261029100147-novel.db.sha256
```

前缀是北京时间，精确到秒。单文件上传时前缀加在原文件名前面。

传文件夹时前缀加在目录名上，目录里面的文件保持原名：

```text
20261029100147-备份/
├── 说明.txt
└── 子目录/数据.txt
```

时间戳前缀是必需项，不是风格偏好：`kuake` 遇到同名文件会静默跳过，
连大小都不比对，还返回成功。唯一的办法是让文件名永不重复。

`.sha256` 是拉库时的内容凭证。没有它就只核对字节数。

注意：这个摘要是**快照文件**的，不是源库的。`.backup` 复制的是页，不是字节，
所以快照的 sha256 与 `apps/server/db/database/novel.db` 的 sha256 必然不同。
两者不能互相比对，只能各自比自己的那一份。

## 一次完整流程

本地机器：

```bash
uv run websync          # 按 2 选本地文件，按 u 上传
```

云端机器：

```bash
uv run websync          # 按 1 选远端文件，按 d 下载
```

落地的是 `20261029100147-novel.db`，不是 `novel.db`：

1. 停掉 trans-app。
2. 把落地文件改名成 `novel.db`，替换 `apps/server/db/database/` 下的同名文件。
3. 启动 trans-app。

WebSync 不会替你改名，也不会去替换运行中的库文件。
运行中的 better-sqlite3 持有文件句柄，就地覆盖会写坏库。

## 设计约束

调研结论落到了具体代码，改动时不要绕开：

| 约束 | 落点 |
|---|---|
| `kuake` 同名静默跳过 | 上传前先查远端同名，存在即拒绝 |
| 上传成功但立刻 list 看不到 | 核对字节数前退避重试 |
| 进度只在 stderr，且是 `\r` 覆盖行 | 按 `\r` 与 `\n` 双分隔解析 |
| `goquark` 失败仍退出 0 | 判定只认输出里的 `OK path=` |
| `goquark` 配置目录只读就报错 | 复制配置到缓存目录再跑 |
| `goquark ls` 只回 100 条且不报截断 | 列表一律用 `kuake list --stream` |
| `kuake` 不能下大文件 | 拉库固定走 `goquark` |
| 各机时区不一致 | 文件名与展示一律用固定 `+08:00` |
| 复制库文件会拿到撕裂副本 | 用 `sqlite3 .backup` 导出 |
| `kuake` 自动读当前目录的 `.env` | 显式设 `KUAKE_LOAD_DOTENV=0` |
| 上传成功但返回 `SKIPPED` | 解析 stdout 信封，要求 `code == "OK"` 且 `size` 相符 |
| 凭证副本落盘为 0644 | 复制到 0700 目录、文件 0600 |
| 命令无法回溯 | 所有外部命令在 `~/.cache/websync/logs/websync.log` 记一条 |

## 测试

```bash
uv run pytest
```

测试全程离线。`kuake` 与 `goquark` 用 `test/fakes/` 下的替身，
只读写临时目录。`sqlite3` 用真实的，但只碰临时文件。

## 排障

| 现象 | 原因 |
|---|---|
| `找不到 kuake` | 二进制不在 PATH，也不在 `~/.local/bin`；在 `[tools]` 写绝对路径 |
| `远端目录` 检查失败 | 凭证过期。`goquark login` 后再跑 `./install.sh`，把新登录态收进 `config/goquark.json` |
| `下载失败` 但没报错 | 看日志里的原始输出，通常是配额或文件已被删 |
| `install.sh` 卡在 `== kuake` | GitHub 不通。把别处可用的 `bin/` 拷过来，或手工下载 |
| 上传后远端没有 | 极少见；WebSync 会重试多次，仍失败就报出来，不静默放过 |
