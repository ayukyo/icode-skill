#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

# Runtime/OpenAI SDK 必须保持可选；既有控制面在屏蔽 site-packages 时仍可启动。
if python3 -S tools/icode_control.py --help >/dev/null \
  && ! rg -q '(^|[[:space:]])(from|import)[[:space:]]+(openai|icode_agent|agent_runtime)' \
       tools/icode_control.py; then
  ok "控制面不依赖 Agent Runtime 或 OpenAI SDK"
else
  bad "控制面被 Agent Runtime/provider 依赖污染"
fi

if rg -q 'Claude Code' references/host_adapters.md \
  && rg -q 'Codex' references/host_adapters.md \
  && rg -q '所有步骤.*主会话' SKILL.md; then
  ok "Codex/Claude Code 既有主会话入口保持"
else
  bad "宿主兼容锚点缺失"
fi

if ! rg -q 'pip install.*openai|bootstrap.*agent|agent_runtime.*install' \
      install.sh scripts/sync-to-global.sh steps/install.md \
  && ./scripts/sync-to-global.sh --dry-run --client all >/dev/null; then
  ok "安装与同步不强制安装模型 SDK"
else
  bad "安装/同步意外引入 Agent 强依赖"
fi

if python3 tools/icode_agent.py status --help >/dev/null \
  && python3 -S tools/icode_agent.py status --help >/dev/null; then
  ok "无第三方 site-packages 时 Agent 只读入口可启动"
else
  bad "Agent CLI 顶层错误导入可选 SDK"
fi

if [ -s steps/ui.md ] \
  && rg -q '/icode ui' SKILL.md \
  && rg -q 'steps/ui\.md' SKILL.md \
  && python3 tools/icode_agent.py ui --help 2>/dev/null | grep -q -- '--dir'; then
  ok "/icode ui 路由存在且保留高级 --dir"
else
  bad "/icode ui 路由或高级兼容入口缺失"
fi

if ! python3 tools/icode_agent.py ui --help 2>/dev/null \
      | rg -q -- '--dir[^\n]*(required|必需)' \
  && rg -q '127\.0\.0\.1:8765' steps/ui.md agent_runtime/README.md \
  && rg -q '端口.*占用.*自动' steps/ui.md agent_runtime/README.md; then
  ok "UI 默认无参数、8765 优先且占用自动回退"
else
  bad "UI 简单启动合同不完整"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
