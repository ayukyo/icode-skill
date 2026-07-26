#!/usr/bin/env bash
# serena 卸载: 从 ~/.claude.json 移除 MCP server
# 使用:
#   ./uninstall.sh         # 移除注册
set -e

SERVER_NAME="serena"

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

echo "🧹 卸载 serena"
# 让 Python 自己解析 ~/.claude.json(避免 bash 传 MSYS 路径给 Python 引发 FileNotFoundError)
"$PYTHON_BIN" - <<PYEOF
import json
import sys
from pathlib import Path

# 强制 UTF-8 stdout,兼容 Windows 默认 GBK 控制台(emoji 报错)
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
