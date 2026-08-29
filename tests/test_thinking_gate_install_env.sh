#!/usr/bin/env bash
# P3 安装配置：sequential-thinking 默认注入 DISABLE_THOUGHT_LOGGING=true（隐私基线）
# + 用户显式开启日志（false）时不被 install 反复覆盖 + 回读校验 + Codex 导出 env 同步
# + 不影响其他 npx 系 MCP（context7/memory/playwright 无 env）。
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

REG="mcp/sequential-thinking/scripts/register_mcp.py"
NODE="$(command -v node)"
NPX="$(command -v npx)"

# 1) 默认注入：空 HOME 下注册 → env.DISABLE_THOUGHT_LOGGING == "true"，且回读一致
HOME1="$(mktemp -d)"
(
  export HOME="$HOME1"
  mkdir -p "$HOME/.claude"
  python3 "$REG" "$NODE" "$NPX" >/dev/null 2>&1
)
OUT="$(python3 - "$HOME1" <<'PY' 2>/dev/null
import json, pathlib, sys
home = pathlib.Path(sys.argv[1])
cfg = json.loads((home / ".claude.json").read_text())
print(cfg["mcpServers"]["sequential-thinking"]["env"]["DISABLE_THOUGHT_LOGGING"])
PY
)"
if [ "$OUT" = "true" ]; then
  ok "默认注册注入 DISABLE_THOUGHT_LOGGING=true"
else
  bad "默认注册未注入 DISABLE_THOUGHT_LOGGING=true（got: $OUT）"
fi
# Codex 导出 entry 也带 env
if [ -f "$HOME1/.claude/icode_data/mcp_entries/sequential-thinking.json" ] && \
   grep -q '"DISABLE_THOUGHT_LOGGING": "true"' "$HOME1/.claude/icode_data/mcp_entries/sequential-thinking.json"; then
  ok "Codex 导出 entry 同步 env"
else
  bad "Codex 导出 entry 缺 env"
fi
rm -rf "$HOME1"

# 2) 用户显式开启日志（false）：重复 install 不得覆盖回 true
HOME2="$(mktemp -d)"
python3 - "$HOME2" <<'PY'
import json, pathlib, sys
home = pathlib.Path(sys.argv[1])
cfg = {"mcpServers": {"sequential-thinking": {
  "command": "/usr/bin/npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
  "env": {"DISABLE_THOUGHT_LOGGING": "false"}}}}
(home / ".claude.json").write_text(json.dumps(cfg), encoding="utf-8")
(home / ".claude").mkdir(parents=True, exist_ok=True)
PY
(
  export HOME="$HOME2"
  python3 "$REG" "$NODE" "$NPX" >/dev/null 2>&1
)
OUT2="$(python3 - "$HOME2" <<'PY' 2>/dev/null
import json, pathlib, sys
home = pathlib.Path(sys.argv[1])
cfg = json.loads((home / ".claude.json").read_text())
print(cfg["mcpServers"]["sequential-thinking"]["env"]["DISABLE_THOUGHT_LOGGING"])
PY
)"
if [ "$OUT2" = "false" ]; then
  ok "用户 DISABLE_THOUGHT_LOGGING=false 不被 install 覆盖"
else
  bad "用户 DISABLE_THOUGHT_LOGGING=false 被覆盖（got: $OUT2）"
fi
rm -rf "$HOME2"

# 3) 其余 npx 系 MCP 不带 env（不受 DISABLE_THOUGHT_LOGGING 影响）
OUT3="$(python3 - <<'PY' 2>/dev/null
import sys
sys.path.insert(0, "mcp/_lib")
from platform_entry import build_server_entry
assert "env" not in build_server_entry("/usr/bin/node", "/usr/bin/npx", "pkg")
e = build_server_entry("/usr/bin/node", "/usr/bin/npx", "pkg", {"DISABLE_THOUGHT_LOGGING": "true"})
assert e["env"] == {"DISABLE_THOUGHT_LOGGING": "true"}
print("ok")
PY
)"
if [ "$OUT3" = "ok" ]; then
  ok "其他 npx 系 MCP 不受 env 注入影响（仅 sequential-thinking 带 env）"
else
  bad "其他 npx 系 MCP 受影响"
fi

echo ""
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
