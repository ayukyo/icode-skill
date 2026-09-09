#!/usr/bin/env bash
set -e
NAME="${1:?缺少 MCP 名称}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then PYTHON_BIN="$(command -v "$candidate")"; break; fi
done
if [ -z "$PYTHON_BIN" ]; then echo "❌ 未找到可用 Python" >&2; exit 1; fi
"$PYTHON_BIN" "$HERE/claude_registry.py" unregister "$NAME"
echo "👉 已移除 $NAME 注册；安装目录和用户配置保留，可恢复。"
