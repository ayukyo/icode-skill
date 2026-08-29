#!/usr/bin/env bash
# 契约测试：快速模式风险自动升级（fast risk upgrade）
#
# 覆盖 WORKFLOW_OPTIMIZATION_PROPOSAL.md §7.1-5 / §7.2-2：
#   负向：快速模式命中异步状态和恢复风险 → 应自动升级完整模式（lint 判违规若未升级）
#   正向：单文件纯计算修改，无持久化和外部行为变化 → 保留快速模式
#   边界：override 记录风险接受可保持 fast
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
LINT="python3 tools/lint_workflow_contract.py"

check_contains() {
  if grep -q "$2" "$1" 2>/dev/null; then ok "$3"; else bad "$3 ($1 缺: $2)"; fi
}
check_contains SKILL.md "risk_profile" "SKILL.md 定义 risk_profile"
check_contains references/dir_and_metadata.md "risk_profile" "dir_and_metadata.md 定义 risk_profile 字段族"
check_contains mcp/workflow-gate/gates.json "fast_risk_upgrade" "gates.json 含 fast_risk_upgrade"
check_contains mcp/workflow-gate/gates.json "async_observer_or_cache" "gates.json 含 async_observer_or_cache 风险标志"
check_contains steps/fast.md "risk_profile" "fast 步骤写 risk_profile 自动升级"
check_contains steps/fast.md "effective_mode" "fast 步骤记录 effective_mode"

make_ticket() {
  mkdir -p "$1"
  printf '%s\n' "$2" > "$1/.ico_metadata.json"
}

# 负向：mode=fast 命中异步/恢复风险，effective_mode=fast 且未 override → 违规
make_ticket "$TMP/n1_risk_unupgraded" '{
  "ticket_id":"WF-FR-N1","workflow_gate_schema_version":1,"mode":"fast",
  "risk_profile":{
    "requested_mode":"fast","effective_mode":"fast",
    "triggers":["async_observer_or_cache","restart_replay_semantics"],
    "risk_flags":{"async_observer_or_cache":true,"restart_replay_semantics":true},
    "override":false
  }
}'
if $LINT "$TMP/n1_risk_unupgraded" --step fast --json >/dev/null 2>&1; then
  bad "N1 fast 命中风险但未升级应判违规"
else
  ok "N1 fast 命中风险未升级 → 违规（应自动升级 full）"
fi

# 正向：fast 命中风险但已自动升级 effective_mode=full → 合规
make_ticket "$TMP/p1_upgraded" '{
  "ticket_id":"WF-FR-P1","workflow_gate_schema_version":1,"mode":"fast",
  "risk_profile":{
    "requested_mode":"fast","effective_mode":"full",
    "triggers":["async_observer_or_cache"],
    "risk_flags":{"async_observer_or_cache":true},
    "override":false
  }
}'
if $LINT "$TMP/p1_upgraded" --step fast --json >/dev/null 2>&1; then
  ok "P1 fast 命中风险已升级 full → 合规"
else
  bad "P1 已升级 full 不应违规"
fi

# 边界：override=true 记录风险接受可保持 fast
make_ticket "$TMP/e1_override" '{
  "ticket_id":"WF-FR-E1","workflow_gate_schema_version":1,"mode":"fast",
  "risk_profile":{
    "requested_mode":"fast","effective_mode":"fast",
    "triggers":["real_env_verification"],
    "risk_flags":{"real_env_verification":true},
    "override":true
  }
}'
if $LINT "$TMP/e1_override" --step fast --json >/dev/null 2>&1; then
  ok "E1 override=true 记录风险接受 → 允许保持 fast"
else
  bad "E1 override=true 不应违规"
fi

# 正向：低风险单文件纯计算（无风险标志）→ 保留 fast
make_ticket "$TMP/p2_lowrisk" '{
  "ticket_id":"WF-FR-P2","workflow_gate_schema_version":1,"mode":"fast",
  "risk_profile":{
    "requested_mode":"fast","effective_mode":"fast",
    "triggers":[],"risk_flags":{"cross_component":false},
    "override":false
  }
}'
if $LINT "$TMP/p2_lowrisk" --step fast --json >/dev/null 2>&1; then
  ok "P2 低风险单文件纯计算 → 保留 fast"
else
  bad "P2 低风险不应升级"
fi

# 边界：full 模式不受 fast_risk gate 约束
make_ticket "$TMP/e2_full" '{
  "ticket_id":"WF-FR-E2","workflow_gate_schema_version":1,"mode":"full"
}'
if $LINT "$TMP/e2_full" --step fast --json >/dev/null 2>&1; then
  ok "E2 full 模式不受 fast_risk 约束"
else
  bad "E2 full 模式不应被 fast_risk gate 误伤"
fi

echo ""
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
