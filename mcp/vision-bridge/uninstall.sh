#!/usr/bin/env bash
# vision-bridge 卸载: 从 ~/.claude.json 移除 MCP + 可选删 target 目录
# 使用:
#   ./uninstall.sh                # 只移除注册（保留代码与 venv, 方便重装）
#   ./uninstall.sh --purge        # 额外删 target 目录与 venv
set -e
CLAUDE_JSON="$HOME/.claude.json"
TARGET="${VISION_BRIDGE_TARGET:-$HOME/.claude/skills/icode/mcp/vision-bridge}"

echo "🧹 卸载 vision-bridge"
if [ -f "$CLAUDE_JSON" ]; then
  python3 - <<PYEOF
import json
p = "$CLAUDE_JSON"
cfg = json.loads(open(p).read())
removed = cfg.get("mcpServers", {}).pop("vision-bridge", None)
if removed:
    open(p, "w").write(json.dumps(cfg, ensure_ascii=False, indent=2))
    print("✅ 已从 ~/.claude.json 移除 vision-bridge")
else:
    print("ℹ️ ~/.claude.json 未注册 vision-bridge, 跳过")
PYEOF
else
  echo "ℹ️ ~/.claude.json 不存在, 跳过"
fi

if [ "${1:-}" = "--purge" ]; then
  if [ -d "$TARGET" ]; then
    rm -rf "$TARGET"
    echo "🗑 已删 $TARGET (含 venv)"
  fi
fi
echo ""
echo "👉 重启 Claude Code 生效"
