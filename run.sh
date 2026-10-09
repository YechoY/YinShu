#!/usr/bin/env bash
# 音枢（Yinshu）Linux / macOS / WSL 一键启动脚本。
# Windows 用户用 run.ps1（PowerShell）。
#
# 用法：
#   ./run.sh                          # 默认 127.0.0.1:8000
#   ./run.sh --host 0.0.0.0           # 手机客户端局域网联调
#   ./run.sh --port 9000              # 自定义端口
# 首次运行会自动安装 uv 并配好依赖，之后每次直接启动。
# 参数原样透传给服务端（见 `uv run yinshu --help`）。
set -euo pipefail
cd "$(dirname "$0")"

# ---- 1. 确保 uv 存在（没有就自动装） ----
if ! command -v uv >/dev/null 2>&1; then
  echo "[yinshu] 未找到 uv，正在自动安装..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    echo "[yinshu] uv 安装完成，但需要重开一个终端再运行 ./run.sh" >&2
    exit 1
  fi
fi

# ---- 2. 同步依赖（首次 clone 后自动装齐；有锁文件用 --frozen 保证可复现） ----
uv sync --frozen >/dev/null 2>&1 || uv sync

# ---- 3. 启动服务（参数原样透传） ----
exec uv run yinshu "$@"
