#!/usr/bin/env bash
# 一键安装 WebSync：uv、Python 环境、kuake、goquark。
#
#   ./install.sh                 # 装本项目，可重复执行
#   ./install.sh --force         # 重装两个外部工具
#   INSTALL_DIR=/opt/bin ./install.sh
#
# 已经装好的东西一律跳过，不重复下载。
# 装完只需要另一个命令：./start_webSync.sh
# 支持 Linux / macOS，amd64 与 arm64。两个外部客户端都是 AGPL-3.0。

set -euo pipefail

cd "$(dirname "$0")"

INSTALL_DIR="${INSTALL_DIR:-$PWD/bin}"
KUAKE_REPO="zhangjingwei/kuake_cli"
GOQUARK_REPO="ButterFuture/GoQuark"
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help)
      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

need() { command -v "$1" >/dev/null 2>&1 || { echo "缺少命令：$1" >&2; exit 1; }; }
need curl

# 本机若有网络加速脚本，下载前先加载。它会设上 proxy 变量，
# 而 curl 走这个代理反而连不上，所以加载完立刻清掉。
# 只在这个文件存在时生效，别的机器上什么都不做。
setup_network() {
  [ -f /etc/network_turbo ] || return 0
  # shellcheck disable=SC1091
  . /etc/network_turbo
  unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
  echo "已加载 /etc/network_turbo，并清掉 proxy 变量"
}

# uv 是唯一的外部依赖。没有就装：装在 ~/.local/bin，不用 sudo。
ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  if [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
    return 0
  fi

  echo "未找到 uv，开始安装（官方脚本，装到 ~/.local/bin）"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || {
    echo "uv 安装失败。手工装：https://docs.astral.sh/uv/" >&2
    exit 1
  }
  echo "uv 已装：$(command -v uv)"
}

# 在安装目录里找已经可用的二进制。裸名优先，其次认带版本号的名字
# （kuake-v1.5.0-linux-amd64 这种）。
#
# 不要求预设的可执行位：压缩包、网盘、U 盘拷贝经常把它丢掉，
# 那种情况下文件是好的，只是 chmod 没跟过来。空文件才当作没有。
existing_tool() {
  local name="$1" candidate
  for candidate in "$INSTALL_DIR/$name" "$INSTALL_DIR/$name"-v* "$INSTALL_DIR/$name"_*; do
    [ -f "$candidate" ] && [ -s "$candidate" ] || continue
    if [ ! -x "$candidate" ]; then
      chmod +x "$candidate" 2>/dev/null || true
    fi
    [ -x "$candidate" ] || continue
    printf '%s\n' "$candidate"
    return 0
  done
  return 1
}

# 两个工具的 version 输出形态不同，各自解析。一律用 sed 读到底：
# pipefail 下 head 提前关管道会让脚本以 141 退出。
kuake_version_of() { "$1" version 2>&1 | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p'; }
goquark_version_of() { "$1" version 2>&1 | sed -n '1p'; }

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

latest_tag() {
  # 没网时必须自己超时退出，不能挂在这里等。curl 默认不限时。
  curl -sIL --connect-timeout 10 --max-time 20 "https://github.com/$1/releases/latest" \
    | awk 'tolower($1)=="location:" { u=$2 } END { print u }' \
    | sed 's#.*/tag/##' | tr -d '\r'
}

# GitHub 取不到版本时给两条能走通的路，而不是直接失败。
explain_offline() {
  local repo="$1" name="$2"
  echo "取不到 $name 的最新版本：GitHub 不可达或超时。" >&2
  echo "两条路：" >&2
  echo "  1. 把别的机器上 webSync/bin/ 整个拷过来，再跑 ./install.sh，会直接跳过下载" >&2
  echo "  2. 手工下载 https://github.com/$repo/releases 里的对应资产放进 $INSTALL_DIR" >&2
}

# 工具没找到但目录里确实有东西时，把目录内容打出来，便于判断是名字不对还是权限丢了。
report_dir() {
  [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ] || return 0
  echo "安装目录里没有可用的 $1，当前内容："
  ls -l "$INSTALL_DIR" | sed 's/^/  /'
}

install_kuake() {
  local found tag asset url
  if [ "$FORCE" -eq 0 ] && found=$(existing_tool kuake); then
    echo "已有：$found"
    echo "版本：$(kuake_version_of "$found")   跳过下载（--force 重装）"
    return 0
  fi

  report_dir kuake
  tag=$(latest_tag "$KUAKE_REPO")
  [ -n "$tag" ] || { explain_offline "$KUAKE_REPO" kuake; exit 1; }
  asset="kuake-${tag}-${os}-${arch}"
  url="https://github.com/$KUAKE_REPO/releases/download/${tag}/${asset}"
  echo "版本：$tag   资产：$asset"

  if ! curl -fsSL --retry 3 --connect-timeout 15 --max-time 600 -o "$INSTALL_DIR/kuake.part" "$url"; then
    rm -f "$INSTALL_DIR/kuake.part"
    echo "下载失败：$url" >&2
    echo "该平台可能没有预编译资产，见 https://github.com/$KUAKE_REPO/releases" >&2
    exit 1
  fi
  chmod +x "$INSTALL_DIR/kuake.part"
  mv -f "$INSTALL_DIR/kuake.part" "$INSTALL_DIR/kuake"
  echo "已装：$INSTALL_DIR/kuake（上游未发布校验文件，无法校验摘要）"
}

install_goquark() {
  local found tag version asset base expected actual
  if [ "$FORCE" -eq 0 ] && found=$(existing_tool goquark); then
    echo "已有：$found"
    echo "版本：$(goquark_version_of "$found")   跳过下载（--force 重装）"
    return 0
  fi

  report_dir goquark
  tag=$(latest_tag "$GOQUARK_REPO")
  [ -n "$tag" ] || { explain_offline "$GOQUARK_REPO" goquark; exit 1; }
  version="${tag#v}"
  asset="goquark_${version}_${os}_${arch}"
  base="https://github.com/$GOQUARK_REPO/releases/download/${tag}"
  echo "版本：$tag   资产：$asset"

  if ! curl -fsSL --retry 3 --connect-timeout 15 --max-time 600 -o "$INSTALL_DIR/goquark.part" "$base/$asset"; then
    rm -f "$INSTALL_DIR/goquark.part"
    echo "下载失败：$base/$asset" >&2
    echo "该平台可能没有预编译资产，见 https://github.com/$GOQUARK_REPO/releases" >&2
    exit 1
  fi

  # 上游发布了 SHA256SUMS，校验不过就不落地。
  if ! curl -fsSL --retry 3 --connect-timeout 15 --max-time 60 -o "$INSTALL_DIR/SHA256SUMS.part" "$base/SHA256SUMS"; then
    rm -f "$INSTALL_DIR/goquark.part" "$INSTALL_DIR/SHA256SUMS.part"
    echo "取不到 SHA256SUMS，拒绝安装未经校验的二进制" >&2
    exit 1
  fi
  expected=$(awk -v a="$asset" '$2==a {print $1}' "$INSTALL_DIR/SHA256SUMS.part")
  rm -f "$INSTALL_DIR/SHA256SUMS.part"
  if [ -z "$expected" ]; then
    rm -f "$INSTALL_DIR/goquark.part"
    echo "SHA256SUMS 里没有 $asset" >&2
    exit 1
  fi

  actual=$(sha256_file "$INSTALL_DIR/goquark.part")
  if [ "$actual" != "$expected" ]; then
    rm -f "$INSTALL_DIR/goquark.part"
    echo "摘要不匹配，拒绝安装" >&2
    echo "  期望 $expected" >&2
    echo "  实际 $actual" >&2
    exit 1
  fi

  chmod +x "$INSTALL_DIR/goquark.part"
  mv -f "$INSTALL_DIR/goquark.part" "$INSTALL_DIR/goquark"
  echo "已装：$INSTALL_DIR/goquark（SHA256SUMS 校验通过）"
}

# 登录态放进项目，整包复制到别的机器才能直接用。
# 目标已存在就不动：那台机器可能已经登录过自己的账号。
seed_login() {
  local target="config/goquark.json" source="$HOME/.config/goquark/config.json"
  if [ -s "$target" ]; then
    echo "已有：$target   跳过"
    return 0
  fi
  if [ -s "$source" ]; then
    mkdir -p config
    cp "$source" "$target"
    chmod 600 "$target"
    echo "已从 $source 复制到 $target（权限 600）"
    return 0
  fi
  echo "登录态还没有：$target" >&2
  echo "两条路：" >&2
  echo "  1. 本机跑一次 goquark login，再跑一次 ./install.sh" >&2
  echo "  2. 把已登录机器的 $source 拷成 $target" >&2
  echo "缺它只能装工具，推库拉库都会报「kuake 没有凭证」。" >&2
}

case "$(uname -s)" in
  Linux)  os=linux ;;
  Darwin) os=darwin ;;
  *) echo "不支持的系统：$(uname -s)" >&2; exit 1 ;;
esac

case "$(uname -m)" in
  x86_64|amd64)  arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) echo "不支持的架构：$(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "$INSTALL_DIR"
setup_network
ensure_uv

echo "平台：$os/$arch   安装目录：$INSTALL_DIR"
echo
echo "== kuake（上传） =="
install_kuake

echo
echo "== goquark（取回） =="
install_goquark

echo
echo "== Python 环境 =="
if [ -x ".venv/bin/websync" ]; then
  echo "已存在 .venv，按 uv.lock 核对（不会联网装依赖）"
else
  echo "创建 .venv 并按 uv.lock 装依赖"
fi
uv sync --frozen --quiet

echo
echo "== 登录态 =="
seed_login

echo
echo "== 自检 =="
for tool in kuake goquark; do
  existing_tool "$tool" >/dev/null || { echo "$tool 未落盘到 $INSTALL_DIR" >&2; exit 1; }
done
KUAKE_BIN=$(existing_tool kuake)
GOQUARK_BIN=$(existing_tool goquark)
printf '%-8s %s\n' kuake "$(kuake_version_of "$KUAKE_BIN")"
printf '%-8s %s\n' goquark "$(goquark_version_of "$GOQUARK_BIN")"
printf '%-8s %s\n' websync "$(uv run --frozen websync --version)"

echo
if [ "$INSTALL_DIR" = "$PWD/bin" ]; then
  echo "两个工具都装在项目里，不依赖 PATH。"
else
  echo "注意：装到了项目之外，WebSync 需要它在 PATH 里才能找到。"
  case ":$PATH:" in
    *":$INSTALL_DIR:"*) ;;
    *) echo "  追加：export PATH=\"$INSTALL_DIR:\$PATH\"" ;;
  esac
fi

echo
echo "装完了。启动：./start_webSync.sh"
echo "登录态在 config/goquark.json，随项目复制走。"
