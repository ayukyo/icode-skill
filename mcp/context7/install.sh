#!/usr/bin/env bash
# context7 一键注册 MCP server 到 ~/.claude.json
# 使用:
#   ./install.sh                  # 注册默认包 @upstash/context7-mcp
#   ./install.sh <npm_package>    # 注册其他实现包
#   ./install.sh --uninstall      # 取消注册
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"

# 守卫: 防止脚本被复制到其他目录错误执行。
# 合法工程根必须同时存在 scripts/register_mcp.py。
if [ ! -f "$HERE/scripts/register_mcp.py" ]; then
  echo "❌ 当前目录不是 context7 工程根目录"
  echo "   source: $HERE"
  echo "   请 cd 到 mcp/context7 目录后重跑"
  exit 1
fi

# 探测 Python 解释器,避免 Git Bash 下命中 WindowsApps 的 python3 stub。
# WindowsApps stub 跑 --version 无输出但 exit 0;真 Python 必有版本字符串。
# Linux/macOS 的 python3 --version 永远有输出,逻辑等价无副作用。
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

# 探测 Node 与 npx。
# Linux/macOS 通常有 node + npx；Windows 上 npx 可能是 npx.cmd。
# command -v 在 PATH 上查找,优先匹配用户实际安装的二进制。
NODE_BIN="$(command -v node 2>/dev/null || command -v nodejs 2>/dev/null || true)"
NPX_BIN="$(command -v npx 2>/dev/null || true)"
if [ -z "$NODE_BIN" ]; then
  echo "❌ 未找到 node 命令。请安装 Node.js (https://nodejs.org)"
  exit 1
fi
if [ -z "$NPX_BIN" ]; then
  echo "❌ 未找到 npx 命令。npx 通常随 npm 一起装。"
  echo "   Linux:   sudo apt install npm"
  echo "   macOS:   brew install node  (含 npm)"
  echo "   Windows: 重装 Node.js (https://nodejs.org)"
  exit 1
fi

echo "📦 context7 安装"
echo "   node: $NODE_BIN"
echo "   npx:  $NPX_BIN"
echo "   pkg:  ${1:-@upstash/context7-mcp}"

# 卸载分支
if [ "${1:-}" = "--uninstall" ]; then
  "$HERE/uninstall.sh"
  exit 0
fi

# 注册到 ~/.claude.json
# 仅在用户显式传参时才传第 4 个参数(包名);无参时让 register_mcp.py 用默认值,
# 避免 ${1:-} 展开成空串覆盖默认包名。
REGISTER_ARGS=("$NODE_BIN" "$NPX_BIN")
if [ -n "${1:-}" ]; then
  REGISTER_ARGS+=("$1")
fi
"$PYTHON_BIN" "$HERE/scripts/register_mcp.py" "${REGISTER_ARGS[@]}"

echo ""
echo "🎉 完成！"
echo "   首次使用 npx 会自动下载包到 npm 缓存。"
echo "   ⚠️ 重启 Claude Code 让 MCP 注册生效。"
