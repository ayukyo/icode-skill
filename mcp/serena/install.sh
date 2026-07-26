#!/usr/bin/env bash
# serena (LSP 增强 AI 编码) - Python + uv 形态
# 官方包: serena-mcp-server (从 git+https://github.com/oraios/serena 安装)
# 使用:
#   ./install.sh                  # 注册(需要 uv + uvx + Python 3.10+)
#   ./install.sh --uninstall      # 卸载
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"

# 守卫: 合法工程根必须存在 scripts/register_mcp.py
if [ ! -f "$HERE/scripts/register_mcp.py" ]; then
  echo "❌ 当前目录不是 serena 工程根目录"
  echo "   source: $HERE"
  echo "   请 cd 到 mcp/serena 目录后重跑"
  exit 1
fi

# 探测 Python (避免 Git Bash 下命中 WindowsApps 的 python3 stub)
PYTHON_BIN=""
for _py in python3 python; do
  if command -v "$_py" >/dev/null 2>&1; then
    _bin="$(command -v "$_py")"
    if [ -n "$("$_bin" --version 2>&1 | grep -i 'python')" ]; then
      PYTHON_BIN="$_bin"
      break
    fi
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo "❌ 未找到可用的 python 或 python3"
  exit 1
fi

# 探测 Python >= 3.10 (serena 要求)
PY_VER="$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")"
PY_MAJOR="$("$PYTHON_BIN" -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo 0)"
PY_MINOR="$("$PYTHON_BIN" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo 0)"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
  echo "❌ Python >= 3.10 required (current: $PY_VER)"
  exit 1
fi

# 探测 uv (Astral 出品的 Python 包管理工具,含 uvx 子命令)
# 主动安装 uv(若缺失):按平台优先级尝试 brew/curl/winget/PowerShell
# 用 --no-auto-install 跳过自动安装(回到手动提示)
attempt_install_uv() {
  echo "ℹ️  未找到 uv,尝试自动安装..."

  # 探测 OS(uname 在 Git Bash / Linux / macOS 有效;Windows 上 fallback)
  _os="$(uname -s 2>/dev/null || echo Windows)"
  case "$_os" in
    Linux|Darwin)
      # macOS 优先 brew(更快、更幂等)
      if command -v brew >/dev/null 2>&1; then
        echo "  → brew install uv"
        if brew install uv; then return 0; fi
      fi
      # Linux/macOS: 官方脚本(curl)
      if command -v curl >/dev/null 2>&1; then
        echo "  → curl -LsSf https://astral.sh/uv/install.sh | sh"
        if curl -LsSf https://astral.sh/uv/install.sh | sh; then
          # 官方脚本默认装到 ~/.cargo/bin,可能不在 PATH,放到候选
          return 0
        fi
      fi
      ;;
    *)
      # Windows: 优先 winget(系统自带)
      if command -v winget >/dev/null 2>&1; then
        echo "  → winget install --id=astral-sh.uv"
        if winget install --id=astral-sh.uv --accept-source-agreements --accept-package-agreements; then
          return 0
        fi
      fi
      # PowerShell: 官方脚本(PowerShell 5+/7+)
      if command -v powershell >/dev/null 2>&1; then
        echo "  → powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\""
        if powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"; then
          return 0
        fi
      fi
      ;;
  esac
  return 1
}

UV_BIN="$(command -v uv 2>/dev/null || true)"
if [ -z "$UV_BIN" ] && [ "${1:-}" != "--no-auto-install" ]; then
  if attempt_install_uv; then
    # 装完重新探测,带 fallback 路径(Linux 官方脚本装到 ~/.cargo/bin)
    UV_BIN="$(command -v uv 2>/dev/null || true)"
    [ -z "$UV_BIN" ] && [ -x "$HOME/.cargo/bin/uv" ] && UV_BIN="$HOME/.cargo/bin/uv"
    [ -z "$UV_BIN" ] && [ -x "$HOME/.local/bin/uv" ] && UV_BIN="$HOME/.local/bin/uv"
  fi
fi
if [ -z "$UV_BIN" ]; then
  echo "❌ 未找到 uv 命令"
  if [ "${1:-}" = "--no-auto-install" ]; then
    echo "   (--no-auto-install 模式,跳过自动安装)"
  else
    echo "   (自动安装失败)"
  fi
  echo "   手动安装:"
  echo "     • macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "     • Windows:     winget install --id=astral-sh.uv"
  echo "     • Homebrew:    brew install uv"
  echo "   装完重跑 ./install.sh"
  exit 1
fi

# 探测 uvx (uv 的子命令)。uvx 通常随 uv 一起装,这里做 fallback 兜底
UVX_BIN="$(command -v uvx 2>/dev/null || true)"
[ -z "$UVX_BIN" ] && [ -x "$HOME/.cargo/bin/uvx" ] && UVX_BIN="$HOME/.cargo/bin/uvx"
[ -z "$UVX_BIN" ] && [ -x "$HOME/.local/bin/uvx" ] && UVX_BIN="$HOME/.local/bin/uvx"
if [ -z "$UVX_BIN" ]; then
  echo "❌ uvx 未找到。uvx 是 uv 的子命令,如未安装请升级 uv"
  echo "   升级: uv self update"
  exit 1
fi

echo "📦 serena 安装"
echo "   python: $PYTHON_BIN ($PY_VER)"
echo "   uv:     $UV_BIN"
echo "   uvx:    $UVX_BIN"

# 卸载分支
if [ "${1:-}" = "--uninstall" ]; then
  "$HERE/uninstall.sh"
  exit 0
fi

# 注册到 ~/.claude.json
# 用 uvx 启动 serena(首次调用会自动从 git clone + 装包)
"$PYTHON_BIN" "$HERE/scripts/register_mcp.py"

echo ""
echo "🎉 完成!"
echo "   首次调用 uvx 会自动从 git clone serena (约 50MB)。"
echo "   ⚠️ 需要装至少一个 LSP server 才能用:"
echo "      • Python:      pip install pyright"
echo "      • JS/TS:       npm install -g typescript-language-server typescript"
echo "      • C/C++:       系统装 clangd (apt install clangd / brew install clangd)"
echo "      • Rust:        rustup component add rust-analyzer"
echo "      • Java:        系统装 jdtls"
echo "      • Go:          系统装 gopls"
echo "   完整语言支持: https://github.com/oraios/serena"
echo "   ⚠️ 重启 Claude Code 让注册生效。"
