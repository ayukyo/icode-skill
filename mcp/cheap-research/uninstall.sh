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
"$PYTHON_BIN" - <<PYEOF
import json
import sys
from pathlib import Path

# 强制 UTF-8 stdout,兼容 Windows 默认 GBK 控制台
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

cfg_path = Path.home() / ".claude.json"
if not cfg_path.exists():
    print("ℹ️ ~/.claude.json 不存在, 跳过")
else:
    cfg = json.loads(cfg_path.read_text())
    removed = cfg.get("mcpServers", {}).pop("$SERVER_NAME", None)
    if removed:
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
        print(f"✅ 已从 ~/.claude.json 移除 $SERVER_NAME")
    else:
        print(f"ℹ️ ~/.claude.json 未注册 $SERVER_NAME, 跳过")
PYEOF

echo ""
echo "👉 重启 Claude Code 生效"
