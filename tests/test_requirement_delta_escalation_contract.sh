#!/usr/bin/env bash
# 契约测试：需求增量强制升级（requirement delta escalation）
#
# 覆盖 WORKFLOW_OPTIMIZATION_PROPOSAL.md §7.1-3 / §7.2-4：
#   负向：轻量修补中新增重大语义 → 应设置 needs_replan 并停止（lint 阻断）
#   正向：重大增量已完成分流（resolved）→ 允许继续
#   边界：minor 不阻断；classification 词表；severity 判定
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
check_contains SKILL.md "requirement_deltas" "SKILL.md 定义 requirement_deltas（workflow gate 升级）"
check_contains mcp/workflow-gate/gates.json "requirement_delta_escalation" "gates.json 含 requirement_delta_escalation"
check_contains steps/03_merge.md "needs_replan" "merge 检查 needs_replan"
check_contains steps/08_patch.md "severity=major" "patch 检测重大语义变化"
check_contains steps/08_patch.md "needs_replan" "patch 重大增量回流计划"

make_ticket() {
  mkdir -p "$1"
  printf '%s\n' "$2" > "$1/.ico_metadata.json"
}

# 负向：patch 中新增重大语义（severity=major, needs_replan, status=open）→ 阻断
make_ticket "$TMP/n1_major_open" '{
  "ticket_id":"WF-RD-N1","workflow_gate_schema_version":1,
  "requirement_deltas":[
    {"id":"delta-001","summary":"新增多实体传递收敛语义","severity":"major","needs_replan":true,"status":"open"}
  ]
}'
for st in plan code patch merge; do
  if $LINT "$TMP/n1_major_open" --step "$st" --json >/dev/null 2>&1; then
    bad "N1 --step $st 应阻断（重大增量未回流）"
  else
    ok "N1 --step $st 阻断（重大增量未回流）"
  fi
done

# 正向：重大增量已分流（resolved）→ 允许继续
make_ticket "$TMP/p1_major_resolved" '{
  "ticket_id":"WF-RD-P1","workflow_gate_schema_version":1,
  "requirement_deltas":[
    {"id":"delta-001","summary":"新增多实体传递收敛语义","severity":"major","needs_replan":true,"status":"resolved","classification":"needs_replan"}
  ]
}'
if $LINT "$TMP/p1_major_resolved" --step patch --json >/dev/null 2>&1; then
  ok "P1 重大增量已 resolved → 允许"
else
  bad "P1 已 resolved 重大增量不应阻断"
fi

# 边界：minor 且 needs_replan=false → 不阻断
make_ticket "$TMP/e1_minor" '{
  "ticket_id":"WF-RD-E1","workflow_gate_schema_version":1,
  "requirement_deltas":[
    {"id":"d1","summary":"措辞澄清","severity":"minor","needs_replan":false,"status":"open","classification":"clarification_only"}
  ]
}'
if $LINT "$TMP/e1_minor" --step code --json >/dev/null 2>&1; then
  ok "E1 minor 不阻断"
else
  bad "E1 minor 不应阻断"
fi

# 边界：major 但 needs_replan=false（已就地处理且不回流）→ 不阻断（无回流要求）
make_ticket "$TMP/e2_major_no_replan" '{
  "ticket_id":"WF-RD-E2","workflow_gate_schema_version":1,
  "requirement_deltas":[
    {"id":"d1","summary":"入口变化但已在计划中","severity":"major","needs_replan":false,"status":"resolved"}
  ]
}'
if $LINT "$TMP/e2_major_no_replan" --step code --json >/dev/null 2>&1; then
  ok "E2 major 但 needs_replan=false → 不阻断"
else
  bad "E2 major 但 needs_replan=false 不应阻断"
fi

# 边界：classification 词表外 → 阻断
make_ticket "$TMP/e3_bad_class" '{
  "ticket_id":"WF-RD-E3","workflow_gate_schema_version":1,
  "requirement_deltas":[
    {"id":"d1","severity":"minor","needs_replan":false,"status":"open","classification":"bogus"}
  ]
}'
if $LINT "$TMP/e3_bad_class" --json >/dev/null 2>&1; then
  bad "E3 classification 词表外应阻断"
else
  ok "E3 classification=bogus 词表外 → 阻断"
fi

echo ""
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
