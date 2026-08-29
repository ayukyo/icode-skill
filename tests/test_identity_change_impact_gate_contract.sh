#!/usr/bin/env bash
# 契约测试：身份变化影响门禁（identity change impact gate）
#
# 覆盖 WORKFLOW_OPTIMIZATION_PROPOSAL.md §7.1-2 / §7.2-3：
#   负向：身份变化已声明但聚合选择器未填影响结论 → code/deploy 应失败（lint 阻断）
#   正向：引用、索引、选择器和恢复证据齐全 → 允许部署
#   边界：identity_change=false 不强制；9 维每项必答；completeness 词表
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
check_contains SKILL.md "impact_contract" "SKILL.md 定义 impact_contract"
check_contains references/dir_and_metadata.md "impact_contract" "dir_and_metadata.md 定义 impact_contract 字段族"
check_contains mcp/workflow-gate/gates.json "identity_change_gate" "gates.json 含 identity_change_gate"
check_contains mcp/workflow-gate/gates.json "authoritative_writer" "gates.json 9 维清单含 authoritative_writer"
check_contains steps/01_plan.md "impact_contract" "plan 步骤写 impact_contract"
check_contains steps/02_review.md "impact_contract" "review 独立审查影响清单"
check_contains steps/04_code.md "lint_workflow_contract.py" "code 前置门禁跑 workflow gate"

make_ticket() {
  mkdir -p "$1"
  printf '%s\n' "$2" > "$1/.ico_metadata.json"
}

# 负向：identity_change=true 但 completeness=incomplete → code/deploy/audit-verified 阻断
make_ticket "$TMP/n1_incomplete" '{
  "ticket_id":"WF-IC-N1","workflow_gate_schema_version":1,
  "impact_contract":{"identity_change":true,"completeness":"incomplete"}
}'
for st in code deploy audit-verified; do
  if $LINT "$TMP/n1_incomplete" --step "$st" --json >/dev/null 2>&1; then
    bad "N1 --step $st 应阻断（身份变化影响清单不完整）"
  else
    ok "N1 --step $st 阻断"
  fi
done

# 负向：completeness=complete 但 9 维缺一项（queries_and_selectors 未填）→ 阻断
make_ticket "$TMP/n2_missing_dim" '{
  "ticket_id":"WF-IC-N2","workflow_gate_schema_version":1,
  "impact_contract":{
    "identity_change":true,
    "authoritative_writer":{"status":"affected","evidence":"d4"},
    "persistent_references":{"status":"affected","evidence":"t-mig"},
    "derived_metadata":{"status":"affected","evidence":"t-recalc"},
    "runtime_indexes":{"status":"affected","evidence":"t-prune"},
    "async_links":{"status":"not-affected","evidence":"no-observer"},
    "recovery_paths":{"status":"affected","evidence":"t-restart"},
    "external_projections":{"status":"not-affected","evidence":"proto"},
    "rollback_and_failure":{"status":"affected","evidence":"t-rollback"},
    "completeness":"complete"
  }
}'
if $LINT "$TMP/n2_missing_dim" --step deploy --json >/dev/null 2>&1; then
  bad "N2 缺 queries_and_selectors 维度应阻断"
else
  ok "N2 9 维缺一项（聚合选择器未填）→ 阻断"
fi

# 正向：identity_change=true 且 9 维全填 + completeness=complete → 允许部署
make_ticket "$TMP/p1_full" '{
  "ticket_id":"WF-IC-P1","workflow_gate_schema_version":1,
  "impact_contract":{
    "identity_change":true,
    "authoritative_writer":{"status":"affected","evidence":"design-section-4"},
    "persistent_references":{"status":"affected","evidence":"test-dependent-reference-migration"},
    "derived_metadata":{"status":"affected","evidence":"test-recalc"},
    "runtime_indexes":{"status":"affected","evidence":"test-stale-identity-pruned"},
    "queries_and_selectors":{"status":"affected","evidence":"test-select-all-authoritative-only"},
    "async_links":{"status":"not-affected","evidence":"no-observer"},
    "recovery_paths":{"status":"affected","evidence":"test-restart-equivalence"},
    "external_projections":{"status":"not-affected","evidence":"protocol-preserves-survivor-identity"},
    "rollback_and_failure":{"status":"affected","evidence":"test-rollback"},
    "completeness":"complete"
  }
}'
for st in code deploy audit-verified; do
  if $LINT "$TMP/p1_full" --step "$st" --json >/dev/null 2>&1; then
    ok "P1 --step $st 允许（9 维齐全）"
  else
    bad "P1 --step $st 不应阻断"
  fi
done

# 边界：identity_change=false → 不强制
make_ticket "$TMP/e1_nochange" '{
  "ticket_id":"WF-IC-E1","workflow_gate_schema_version":1,
  "impact_contract":{"identity_change":false}
}'
if $LINT "$TMP/e1_nochange" --step deploy --json >/dev/null 2>&1; then
  ok "E1 identity_change=false 不阻断"
else
  bad "E1 identity_change=false 不应阻断"
fi

# 边界：completeness 词表外 → 阻断
make_ticket "$TMP/e2_bad_completeness" '{
  "ticket_id":"WF-IC-E2","workflow_gate_schema_version":1,
  "impact_contract":{"identity_change":true,"completeness":"maybe"}
}'
if $LINT "$TMP/e2_bad_completeness" --step deploy --json >/dev/null 2>&1; then
  bad "E2 completeness 词表外应阻断"
else
  ok "E2 completeness=maybe 词表外 → 阻断"
fi

# 边界：维度 status 词表外 → 阻断
make_ticket "$TMP/e3_bad_status" '{
  "ticket_id":"WF-IC-E3","workflow_gate_schema_version":1,
  "impact_contract":{
    "identity_change":true,"completeness":"complete",
    "authoritative_writer":{"status":"maybe","evidence":"x"},
    "persistent_references":{"status":"not-affected","evidence":"x"},
    "derived_metadata":{"status":"not-affected","evidence":"x"},
    "runtime_indexes":{"status":"not-affected","evidence":"x"},
    "queries_and_selectors":{"status":"not-affected","evidence":"x"},
    "async_links":{"status":"not-affected","evidence":"x"},
    "recovery_paths":{"status":"not-affected","evidence":"x"},
    "external_projections":{"status":"not-affected","evidence":"x"},
    "rollback_and_failure":{"status":"not-affected","evidence":"x"}
  }
}'
if $LINT "$TMP/e3_bad_status" --step deploy --json >/dev/null 2>&1; then
  bad "E3 维度 status 词表外应阻断"
else
  ok "E3 维度 status=maybe 词表外 → 阻断"
fi

echo ""
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
