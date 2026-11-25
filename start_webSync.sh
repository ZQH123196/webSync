#!/usr/bin/env bash
# 启动 WebSync。不带参数进交互界面；带参数原样透传给 websync 子命令。
set -euo pipefail

cd "$(dirname "$0")"

# 配置、暂存、日志全部相对项目根解析。显式钉住，从任何目录调起结果一样。
export WEBSYNC_HOME="$PWD"

# uv 常装在 ~/.local/bin，登录 shell 之外的场景（桌面快捷方式、systemd、cron）
# 往往不在 PATH 里。
if ! command -v uv >/dev/null 2>&1; then
    if [ -x "$HOME/.local/bin/uv" ]; then
        PATH="$HOME/.local/bin:$PATH"
    else
        echo "找不到 uv。装法：curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
        exit 1
    fi
fi

# 没装环境就直接说清楚，不要让 uv 在启动时现场联网装依赖。
if [ ! -x ".venv/bin/websync" ]; then
    echo "Python 环境还没装。先跑：./install.sh" >&2
    exit 1
fi

# 交互界面必须有终端。被重定向或从管道调起时直接拒绝，不要让它画到一半退出。
if [ "$#" -eq 0 ] && [ ! -t 0 ]; then
    echo "交互界面需要终端。" >&2
    echo "只做检查请带子命令：$0 doctor | $0 ls | $0 init" >&2
    exit 2
fi

# --frozen：只用 uv.lock 里的版本。pyproject 与锁不一致时立刻失败，
# 不允许启动期悄悄升级依赖。
exec uv run --frozen websync "$@"
