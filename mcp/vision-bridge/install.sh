#!/usr/bin/env bash
# vision-bridge 一键装/同步到 ~/.claude/skills/icode/mcp/vision-bridge + 注册 MCP
# 使用:
#   ./install.sh                  # 增量同步+注册（开发期改完代码后跑这条同步）
#   ./install.sh --full           # 完整重装: 清旧 venv、删旧安装目录、重新同步
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${VISION_BRIDGE_TARGET:-$HOME/.claude/skills/icode/mcp/vision-bridge}"

# 守卫: 防止脚本被复制到其他目录(如 /tmp)错误执行,导致污染 target。
# 合法工程根必须同时存在 server.py 和 providers/。
if [ ! -f "$HERE/server.py" ] || [ ! -d "$HERE/providers" ]; then
  echo "❌ 当前目录不是 vision-bridge 工程根目录"
  echo "   source: $HERE"
  echo "   请 cd 到 mcp/vision-bridge 目录后重跑"
  exit 1
fi

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
  # 从已安装位置运行时 HERE 与 TARGET 同路径,同步是恒等操作:跳过同文件
  # cp,否则 `cp a a` 报 "are the same file" 使 set -e 中断安装。
  for f in $(find . -type f -not -path './.venv/*' -not -path '*/__pycache__/*' -not -path './config.json'); do
    dst="$TARGET/${f#./}"
    if [ "$HERE/${f#./}" = "$dst" ]; then
      continue
    fi
    mkdir -p "$(dirname "$dst")"
    cp "$f" "$dst"
  done
fi
echo "✅ 代码已同步到 $TARGET"
cd "$TARGET"

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

# 2. venv (在 target 里建, 解耦开发仓)。
#    兜底策略:$PYTHON_BIN -m venv 失败(系统缺 python3-venv 包)→ 降级到 virtualenv
if [ ! -d "$TARGET/.venv" ]; then
  echo "📦 创建 venv..."
  if "$PYTHON_BIN" -m venv "$TARGET/.venv"; then
    :
  else
    echo "⚠ $PYTHON_BIN -m venv 失败(常见: 系统未装 python3-venv 包), 降级到 virtualenv"
    if ! "$PYTHON_BIN" -c "import virtualenv" 2>/dev/null; then
      echo "❌ virtualenv pip 包也未装。请二选一:"
      echo "   • sudo apt install python3.10-venv    # Debian/Ubuntu 标准做法"
      echo "   • pip install --user virtualenv       # 用户级替代"
      echo "装好后重跑 ./install.sh"
      exit 1
    fi
    "$PYTHON_BIN" -m virtualenv "$TARGET/.venv"
  fi
fi

# 探测 venv 可执行目录。Windows venv 在 Scripts/,Unix (Linux/macOS) 在 bin/。
# 这样 install.sh 在两个平台都用同一份脚本,无需 fork。
if [ -d "$TARGET/.venv/Scripts" ]; then
  PYBIN="$TARGET/.venv/Scripts"
else
  PYBIN="$TARGET/.venv/bin"
fi

echo "📦 装依赖..."
"$PYBIN/python" -m pip install --quiet --disable-pip-version-check -U pip

# pip 装包:清华源优先,fallback 默认 PyPI(海外/校园网友好)
pip_install() {
  if "$PYBIN/pip" install --quiet --disable-pip-version-check \
       -i https://pypi.tuna.tsinghua.edu.cn/simple "$@" 2>/dev/null; then
    return 0
  fi
  echo "⚠ 清华源装包失败,重试默认 PyPI..."
  "$PYBIN/pip" install --quiet --disable-pip-version-check "$@"
}
pip_install -r "$TARGET/requirements.txt"

# 3. 引导 config (提前到 ffmpeg 落盘之前, 便于把探测到的 ffmpeg_dir 写进去)
if [ ! -f "$TARGET/config.json" ]; then
  cp "$TARGET/config.example.json" "$TARGET/config.json"
  echo ""
  echo "⚠️ 首次安装：已生成 $TARGET/config.json 模板"
  echo "👉 请编辑 $TARGET/config.json 填 base_url / api_key / model 后重启 Claude Code"
  echo ""
fi

# 2.5. ffmpeg 检测（视频抽关键帧依赖，非硬性阻断）
# PATH 上找不到时回退共享探测: 常见安装根(winget Links/Packages、choco、scoop、
# Program Files、自动 provision 运行时)。探测到的目录写入 config.json 的
# ffmpeg_dir, 运行时据此定位, 不依赖客户端进程的 PATH。
echo ""
FFMPEG_DIR=""
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  echo "✅ ffmpeg 已安装 ($(ffmpeg -version 2>&1 | head -1))"
else
  FFMPEG_DIR="$("$PYTHON_BIN" "$HERE/../_lib/find_ffmpeg.py" 2>/dev/null || true)"
  if [ -n "$FFMPEG_DIR" ]; then
    echo "✅ ffmpeg 已定位到非 PATH 目录: $FFMPEG_DIR"
    "$PYTHON_BIN" - "$TARGET/config.json" "$FFMPEG_DIR" <<'PY'
import json
import sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
data = json.loads(cfg_path.read_text(encoding="utf-8"))
data["ffmpeg_dir"] = sys.argv[2]
cfg_path.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
    echo "   (已写入 config.json 的 ffmpeg_dir, 运行时据此调用)"
  else
    echo "⚠️ ffmpeg 未安装。视频分析不可用；vision-bridge 不会绕过本地抽帧直接上传视频。"
    echo "   安装方法:"
    echo "     • winget install Gyan.FFmpeg      # Windows"
    echo "     • brew install ffmpeg              # macOS"
    echo "     • sudo apt install ffmpeg          # Debian/Ubuntu"
    echo "   装好后重跑 ./install.sh 即可(也可用 ICODE_FFMPEG_DIR 指定已装目录)。"
  fi
fi

# 4. 注册到 ~/.claude.json
PY="$PYBIN/python"
SERVER="$TARGET/server.py"
"$PYTHON_BIN" "$TARGET/scripts/register_mcp.py" "$PY" "$SERVER" "$TARGET"

echo ""
echo "🎉 完成！"
echo "   装好: $TARGET"
echo "   修改工程内代码后, 再次跑 ./install.sh 增量同步即可。"
