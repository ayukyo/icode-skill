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
if [ "$NAME" = "icode-local" ]; then
  _py_ok() { [ "$("$@" -c 'print(1)' 2>/dev/null)" = "1" ]; }
  PROFILE_PYTHON=()
  if [ -n "${ICODE_PYTHON_BIN:-}" ] && _py_ok "$ICODE_PYTHON_BIN"; then
    PROFILE_PYTHON=("$ICODE_PYTHON_BIN")
  elif _py_ok python3; then
    PROFILE_PYTHON=(python3)
  elif _py_ok python; then
    PROFILE_PYTHON=(python)
  elif _py_ok py -3; then
    PROFILE_PYTHON=(py -3)
  fi
  if [ ${#PROFILE_PYTHON[@]} -eq 0 ]; then echo "Python is required for local profile preflight" >&2; exit 1; fi
  "${PROFILE_PYTHON[@]}" "$SOURCE_DIR/profile.py" preflight --target "$TARGET" \
    --hosts "${ICODE_MCP_SELECTED_HOSTS:-claude}"
fi
if [ "$MODE" = "--full" ]; then
  if [ "$NAME" = "icode-local" ]; then
    # Rebuild the one runtime without destroying the validated module profile.
    rm -rf "$TARGET/.venv"
  else
    rm -rf "$TARGET/.venv" "$TARGET"
  fi
elif [ -n "$MODE" ]; then
  echo "❌ 不支持的参数: $MODE" >&2
  exit 2
fi

mkdir -p "$TARGET" "$LIB_TARGET"
if command -v rsync >/dev/null 2>&1; then
  delete_args=(--delete)
  # A unified profile can reference custom config files inside this directory.
  [ "$NAME" != "icode-local" ] || delete_args=()
  rsync -a "${delete_args[@]}" --exclude='.venv/' --exclude='__pycache__/' --exclude='config.json' "$SOURCE_DIR/" "$TARGET/"
  rsync -a --delete --exclude='__pycache__/' "$LIB_SOURCE/" "$LIB_TARGET/"
else
  # 从已安装位置运行时 SOURCE_DIR 与 TARGET 可能同路径(顶层 install.sh
  # 调用的是已安装副本),此时同步是恒等操作:跳过同文件 cp,否则
  # `cp a a` 报 "are the same file" 使 set -e 中断安装。
  while IFS= read -r -d '' source; do
    relative="${source#"$SOURCE_DIR"/}"
    destination="$TARGET/$relative"
    if [ "$source" = "$destination" ]; then
      continue
    fi
    mkdir -p "$(dirname "$destination")"
    cp "$source" "$destination"
  done < <(find "$SOURCE_DIR" -type f -not -path '*/.venv/*' -not -path '*/__pycache__/*' -not -name config.json -print0)
  while IFS= read -r -d '' source; do
    relative="${source#"$LIB_SOURCE"/}"
    destination="$LIB_TARGET/$relative"
    if [ "$source" = "$destination" ]; then
      continue
    fi
    mkdir -p "$(dirname "$destination")"
    cp "$source" "$destination"
  done < <(find "$LIB_SOURCE" -type f -not -path '*/__pycache__/*' -print0)
fi
if [ "$NAME" = "icode-local" ]; then
  "${PROFILE_PYTHON[@]}" "$SOURCE_DIR/stage_payload.py" --source "$SOURCE_DIR" --target "$TARGET"
fi
if [ ! -f "$TARGET/config.json" ]; then
  cp "$TARGET/config.example.json" "$TARGET/config.json"
fi

PYTHON_BIN=()
if [ "$NAME" = "icode-local" ]; then
  # Freeze the validated command, including a possible py -3 launcher prefix.
  PYTHON_BIN=("${PROFILE_PYTHON[@]}")
else
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version 2>&1 | grep -qi python; then
      PYTHON_BIN=("$(command -v "$candidate")")
      break
    fi
  done
fi
if [ ${#PYTHON_BIN[@]} -eq 0 ]; then
  echo "❌ 未找到可用 Python" >&2
  exit 1
fi
if [ ! -d "$TARGET/.venv" ]; then
  "${PYTHON_BIN[@]}" -m venv "$TARGET/.venv"
fi
if [ -x "$TARGET/.venv/bin/python" ]; then
  VENV_PY="$TARGET/.venv/bin/python"
  VENV_PIP="$TARGET/.venv/bin/pip"
else
  VENV_PY="$TARGET/.venv/Scripts/python.exe"
  VENV_PIP="$TARGET/.venv/Scripts/pip.exe"
fi
"$VENV_PIP" install --quiet --disable-pip-version-check -r "$TARGET/requirements.txt"
if [ "$NAME" = "icode-local" ]; then
  "${PYTHON_BIN[@]}" "$TARGET/profile.py" setup-profile --target "$TARGET" \
    --hosts "${ICODE_MCP_SELECTED_HOSTS:-claude}"
fi
"${PYTHON_BIN[@]}" "$LIB_TARGET/register_python_mcp.py" "$NAME" "$VENV_PY" "$TARGET/server.py" "$TARGET" "$CONFIG_ENV"
echo "🎉 $NAME 安装完成: $TARGET"
