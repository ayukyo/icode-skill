#!/usr/bin/env bash
# cheap-research 卸载: 从 ~/.claude.json 移除 MCP server
# 使用:
#   ./uninstall.sh         # 移除注册
set -e

SERVER_NAME="cheap-research"

# 探测 Python 解释器
PYTHON_BIN=""
for _py in python3 python; do
  if command -v "$_py" >/dev/null 2>&1; then
    _bin="$(command -v "$_py")"
    if [ -n "$("$_bin" --version 2>&1 | grep -i 'python')" ]; then
      PYTHON_BIN="$_py"
      break
    fi
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo "❌ 未找到可用的 python 或 python3"
  exit 1
fi

echo "🧹 卸载 cheap-research"
# 共享模块: 原子写 + 损坏保护 + 清理导出 entry(与注册侧对称)
HERE="$(cd "$(dirname "$0")" && pwd)"
"$PYTHON_BIN" "$HERE/../_lib/claude_registry.py" unregister "$SERVER_NAME"

echo ""
echo "👉 重启 Claude Code 生效"
