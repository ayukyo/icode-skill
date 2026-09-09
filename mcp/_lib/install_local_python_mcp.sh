#!/usr/bin/env bash
# 共享安装器: install_local_python_mcp.sh <name> <config-env> <source-dir> [--full]
set -e

NAME="${1:?缺少 MCP 名称}"
CONFIG_ENV="${2:?缺少配置环境变量名}"
SOURCE_DIR="${3:?缺少源码目录}"
MODE="${4:-}"
TARGET_BASE="${ICODE_MCP_TARGET_BASE:-$HOME/.claude/skills/icode/mcp}"
TARGET="$TARGET_BASE/$NAME"
LIB_SOURCE="$(cd "$SOURCE_DIR/../_lib" && pwd)"
LIB_TARGET="$TARGET_BASE/_lib"

if [ ! -f "$SOURCE_DIR/server.py" ] || [ ! -f "$SOURCE_DIR/tools_manifest.json" ]; then
  echo "❌ $NAME 源码目录缺少 server.py 或 tools_manifest.json: $SOURCE_DIR" >&2
  exit 1
fi
if [ "$TARGET" = "/" ] || [ "$TARGET" = "$HOME" ] || [ "$(basename "$TARGET")" != "$NAME" ]; then
  echo "❌ 拒绝不安全的安装目标: $TARGET" >&2
  exit 1
fi
if [ "$MODE" = "--full" ]; then
  rm -rf "$TARGET/.venv" "$TARGET"
elif [ -n "$MODE" ]; then
  echo "❌ 不支持的参数: $MODE" >&2
  exit 2
fi

mkdir -p "$TARGET" "$LIB_TARGET"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete --exclude='.venv/' --exclude='__pycache__/' --exclude='config.json' "$SOURCE_DIR/" "$TARGET/"
  rsync -a --delete --exclude='__pycache__/' "$LIB_SOURCE/" "$LIB_TARGET/"
else
  while IFS= read -r -d '' source; do
    relative="${source#"$SOURCE_DIR"/}"
    destination="$TARGET/$relative"
    mkdir -p "$(dirname "$destination")"
    cp "$source" "$destination"
  done < <(find "$SOURCE_DIR" -type f -not -path '*/.venv/*' -not -path '*/__pycache__/*' -not -name config.json -print0)
  while IFS= read -r -d '' source; do
    relative="${source#"$LIB_SOURCE"/}"
    destination="$LIB_TARGET/$relative"
    mkdir -p "$(dirname "$destination")"
    cp "$source" "$destination"
  done < <(find "$LIB_SOURCE" -type f -not -path '*/__pycache__/*' -print0)
fi
if [ ! -f "$TARGET/config.json" ]; then
  cp "$TARGET/config.example.json" "$TARGET/config.json"
fi

PYTHON_BIN=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version 2>&1 | grep -qi python; then
    PYTHON_BIN="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo "❌ 未找到可用 Python" >&2
  exit 1
fi
if [ ! -d "$TARGET/.venv" ]; then
  "$PYTHON_BIN" -m venv "$TARGET/.venv"
fi
if [ -x "$TARGET/.venv/bin/python" ]; then
  VENV_PY="$TARGET/.venv/bin/python"
  VENV_PIP="$TARGET/.venv/bin/pip"
else
  VENV_PY="$TARGET/.venv/Scripts/python.exe"
  VENV_PIP="$TARGET/.venv/Scripts/pip.exe"
fi
"$VENV_PIP" install --quiet --disable-pip-version-check -r "$TARGET/requirements.txt"
"$PYTHON_BIN" "$LIB_TARGET/register_python_mcp.py" "$NAME" "$VENV_PY" "$TARGET/server.py" "$TARGET" "$CONFIG_ENV"
echo "🎉 $NAME 安装完成: $TARGET"
