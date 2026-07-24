#!/usr/bin/env bash
# vision-bridge 一键装/同步到 ~/.claude/skills/icode/mcp/vision-bridge + 注册 MCP
# 使用:
#   ./install.sh                  # 增量同步+注册（开发期改完代码后跑这条同步）
#   ./install.sh --full           # 完整重装: 清旧 venv、删旧安装目录、重新同步
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${VISION_BRIDGE_TARGET:-$HOME/.claude/skills/icode/mcp/vision-bridge}"

echo "📦 vision-bridge 安装"
echo "   source: $HERE"
echo "   target: $TARGET"

# 完整重装模式
if [ "${1:-}" = "--full" ]; then
  echo "🧹 --full: 清旧 venv 与旧 target"
  rm -rf "$HERE/.venv" "$TARGET"
fi

# 1. 同步代码到 target (rsync 不存在则用 cp -u)
mkdir -p "$TARGET"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude='.venv/' --exclude='__pycache__/' \
    --exclude='config.json' --exclude='.env' --exclude='.cache/' \
    "$HERE/" "$TARGET/"
else
  cd "$HERE"
  for f in $(find . -type f -not -path './.venv/*' -not -path '*/__pycache__/*' -not -path './config.json'); do
    dst="$TARGET/${f#./}"
    mkdir -p "$(dirname "$dst")"
    cp "$f" "$dst"
  done
fi
echo "✅ 代码已同步到 $TARGET"
cd "$TARGET"

# 2. venv (在 target 里建, 解耦开发仓)。
#    兜底策略:python3 -m venv 失败(系统缺 python3-venv 包)→ 降级到 virtualenv
if [ ! -d "$TARGET/.venv" ]; then
  echo "📦 创建 venv..."
  if python3 -m venv "$TARGET/.venv" 2>/dev/null; then
    :
  else
    echo "⚠ python3 -m venv 失败(常见: 系统未装 python3-venv), 降级到 virtualenv"
    if ! python3 -c "import virtualenv" 2>/dev/null; then
      echo "❌ virtualenv pip 包也未装。请二选一:"
      echo "   • sudo apt install python3.10-venv    # Debian/Ubuntu 标准做法"
      echo "   • pip install --user virtualenv       # 用户级替代"
      echo "装好后重跑 ./install.sh"
      exit 1
    fi
    python3 -m virtualenv "$TARGET/.venv"
  fi
fi
echo "📦 装依赖..."
"$TARGET/.venv/bin/pip" install --quiet --disable-pip-version-check -U pip

# pip 装包:清华源优先,fallback 默认 PyPI(海外/校园网友好)
pip_install() {
  if "$TARGET/.venv/bin/pip" install --quiet --disable-pip-version-check \
       -i https://pypi.tuna.tsinghua.edu.cn/simple "$@" 2>/dev/null; then
    return 0
  fi
  echo "⚠ 清华源装包失败,重试默认 PyPI..."
  "$TARGET/.venv/bin/pip" install --quiet --disable-pip-version-check "$@"
}
pip_install -r "$TARGET/requirements.txt"

# 3. 引导 config
if [ ! -f "$TARGET/config.json" ]; then
  cp "$TARGET/config.example.json" "$TARGET/config.json"
  echo ""
  echo "⚠️ 首次安装：已生成 $TARGET/config.json 模板"
  echo "👉 请编辑 $TARGET/config.json 填 base_url / api_key / model 后重启 Claude Code"
  echo ""
fi

# 4. 注册到 ~/.claude.json
PY="$TARGET/.venv/bin/python"
SERVER="$TARGET/server.py"
python3 "$TARGET/scripts/register_mcp.py" "$PY" "$SERVER" "$TARGET"

echo ""
echo "🎉 完成！"
echo "   装好: $TARGET"
echo "   修改工程内代码后, 再次跑 ./install.sh 增量同步即可。"
