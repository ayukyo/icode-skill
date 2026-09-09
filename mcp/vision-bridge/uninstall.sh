#!/usr/bin/env bash
# vision-bridge 卸载: 从 ~/.claude.json 移除 MCP server
# 使用:
#   ./uninstall.sh         # 移除注册
#   ./uninstall.sh --purge # 移除注册并删除安装 target（保留源码仓）
set -e

SERVER_NAME="vision-bridge"
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${VISION_BRIDGE_TARGET:-$HOME/.claude/skills/icode/mcp/vision-bridge}"
PURGE=0
case "${1:-}" in
  "") ;;
  --purge) PURGE=1 ;;
  *) echo "❌ 未知参数: $1（仅支持 --purge）"; exit 2 ;;
esac

# 探测 Python 解释器(避免 Git Bash 下命中 WindowsApps 的 python3 stub)
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

echo "🧹 卸载 vision-bridge"
# 共享模块: 原子写 + 损坏保护 + 清理导出 entry(与注册侧对称)
"$PYTHON_BIN" "$HERE/../_lib/claude_registry.py" unregister "$SERVER_NAME"

if [ "$PURGE" -eq 1 ]; then
  TARGET_RESOLVED="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$TARGET")"
  HERE_RESOLVED="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$HERE")"
  if [ "$(basename "$TARGET_RESOLVED")" != "vision-bridge" ] || \
     [ "$TARGET_RESOLVED" = "/" ] || [ "$TARGET_RESOLVED" = "$HOME" ] || \
     [ "$TARGET_RESOLVED" = "$HERE_RESOLVED" ]; then
    echo "❌ 拒绝清理不安全的 target: $TARGET_RESOLVED"
    exit 2
  fi
  rm -rf -- "$TARGET_RESOLVED"
  echo "✅ 已清理安装 target: $TARGET_RESOLVED"
fi

echo ""
echo "👉 重启 Claude Code 生效"
