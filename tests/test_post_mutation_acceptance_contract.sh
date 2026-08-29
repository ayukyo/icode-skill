#!/usr/bin/env bash
# 契约测试：生命周期验收合同（post-mutation acceptance contract）
#
# 覆盖 WORKFLOW_OPTIMIZATION_PROPOSAL.md §7.1-4 / §7.1-6 / §7.2-4：
#   负向：即时结果通过但重启结果不同 → 不得标记验证完成（矩阵缺单元 → verified 阻断）
#   负向：验收矩阵缺少依赖归属场景 → 部署门禁应失败（lint --step deploy 阻断）
#   正向：矩阵覆盖 4 阶段 × 4 消费者 → 允许 verified
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
check_contains SKILL.md "acceptance_contract" "SKILL.md 定义 acceptance_contract"
check_contains references/dir_and_metadata.md "acceptance_contract" "dir_and_metadata.md 定义 acceptance_contract 字段族"
check_contains mcp/workflow-gate/gates.json "acceptance_contract" "gates.json 含 acceptance_contract"
check_contains mcp/workflow-gate/gates.json "immediate" "gates.json 验收阶段含 immediate"
check_contains mcp/workflow-gate/gates.json "replay" "gates.json 验收阶段含 replay"
check_contains steps/01_plan.md "acceptance_contract" "plan 步骤写 acceptance_contract 矩阵"
check_contains steps/05_deepcheck.md "生命周期一致性复检" "deepcheck 做生命周期一致性复检"
check_contains steps/06_audit.md "audit-verified" "audit 验收门用 --step audit-verified"

# 生成一条完整 16 单元矩阵（4 阶段 × 4 消费者）
gen_full_matrix() {
  python3 - <<'PY'
import json
phases=["immediate","converged","restart","replay"]
consumers=["direct_query","aggregate_selector","persistent_reference","external_projection"]
cells=[]
for p in phases:
    for c in consumers:
        cells.append({"scenario":"收敛/重启/重放","phase":p,"consumer":c,
                      "expected":"only-surviving" if c!="external_projection" else "same-survivor",
                      "evidence":"t-"+p+"-"+c,"status":"passed"})
print(json.dumps(cells))
PY
}

make_ticket() {
  mkdir -p "$1"
  printf '%s\n' "$2" > "$1/.ico_metadata.json"
}

# 负向：即时通过但重启结果未验证（矩阵只含 immediate）→ verified 应被阻断
MATRIX_PARTIAL='[{"scenario":"部分关联后收敛","phase":"immediate","consumer":"direct_query","expected":"only-surviving","evidence":"t1","status":"passed"},{"scenario":"部分关联后收敛","phase":"immediate","consumer":"aggregate_selector","expected":"only-surviving","evidence":"t2","status":"passed"},{"scenario":"部分关联后收敛","phase":"immediate","consumer":"persistent_reference","expected":"only-surviving","evidence":"t3","status":"passed"},{"scenario":"部分关联后收敛","phase":"immediate","consumer":"external_projection","expected":"only-surviving","evidence":"t4","status":"passed"}]'
make_ticket "$TMP/n1_immediate_only" "{
  \"ticket_id\":\"WF-AC-N1\",\"workflow_gate_schema_version\":1,
  \"impact_contract\":{\"identity_change\":true,\"completeness\":\"complete\"},
  \"acceptance_contract\":{\"requires_lifecycle\":true,\"matrix\":$MATRIX_PARTIAL},
  \"delivery_verdict\":\"verified\"
}"
if $LINT "$TMP/n1_immediate_only" --step deploy --json >/dev/null 2>&1; then
  bad "N1 即时通过但缺重启/重放阶段应阻断"
else
  ok "N1 矩阵缺 restart/replay 阶段 → 阻断 verified"
fi

# 负向：矩阵缺依赖归属消费者（persistent_reference 缺失）→ 部署门禁失败
MATRIX_NO_REF=$(python3 - <<'PY'
import json
phases=["immediate","converged","restart","replay"]
consumers=["direct_query","aggregate_selector","external_projection"]  # 缺 persistent_reference
cells=[{"scenario":"s","phase":p,"consumer":c,"expected":"x","evidence":"t","status":"passed"} for p in phases for c in consumers]
print(json.dumps(cells))
PY
)
make_ticket "$TMP/n2_missing_ref" "{
  \"ticket_id\":\"WF-AC-N2\",\"workflow_gate_schema_version\":1,
  \"impact_contract\":{\"identity_change\":true,\"completeness\":\"complete\"},
  \"acceptance_contract\":{\"requires_lifecycle\":true,\"matrix\":$MATRIX_NO_REF}
}"
if $LINT "$TMP/n2_missing_ref" --step deploy --json >/dev/null 2>&1; then
  bad "N2 矩阵缺依赖归属场景应阻断部署门禁"
else
  ok "N2 缺 persistent_reference 消费者 → 部署门禁失败"
fi

# 正向：矩阵完整（16 单元）+ 身份变化 9 维完整 → 允许 verified
FULL_MATRIX="$(gen_full_matrix)"
# 身份变化 9 维完整清单（identity_change=true 且 completeness=complete 时每项必答）
NINE_DIM='"authoritative_writer":{"status":"affected","evidence":"e1"},"persistent_references":{"status":"affected","evidence":"e2"},"derived_metadata":{"status":"affected","evidence":"e3"},"runtime_indexes":{"status":"affected","evidence":"e4"},"queries_and_selectors":{"status":"affected","evidence":"e5"},"async_links":{"status":"not-affected","evidence":"e6"},"recovery_paths":{"status":"affected","evidence":"e7"},"external_projections":{"status":"not-affected","evidence":"e8"},"rollback_and_failure":{"status":"affected","evidence":"e9"}'
make_ticket "$TMP/p1_full" "{
  \"ticket_id\":\"WF-AC-P1\",\"workflow_gate_schema_version\":1,
  \"impact_contract\":{\"identity_change\":true,\"completeness\":\"complete\",$NINE_DIM},
  \"acceptance_contract\":{\"requires_lifecycle\":true,\"matrix\":$FULL_MATRIX},
  \"delivery_verdict\":\"verified\"
}"
for st in deploy audit-verified; do
  if $LINT "$TMP/p1_full" --step "$st" --json >/dev/null 2>&1; then
    ok "P1 --step $st 矩阵完整 → 允许 verified"
  else
    bad "P1 --step $st 不应阻断（矩阵完整）"
  fi
done

# 边界：不涉及生命周期（无 requires_lifecycle/identity_change）→ 不强制
make_ticket "$TMP/e1_no_lifecycle" '{
  "ticket_id":"WF-AC-E1","workflow_gate_schema_version":1,
  "acceptance_contract":{"requires_lifecycle":false,"matrix":[]}
}'
if $LINT "$TMP/e1_no_lifecycle" --step deploy --json >/dev/null 2>&1; then
  ok "E1 不涉及生命周期 → 不强制矩阵"
else
  bad "E1 不涉及生命周期不应阻断"
fi

# 边界：重复执行不产生额外副作用（replay 全矩阵已含）——正向已在 P1 覆盖
# 边界：matrix 空 cell expected 缺失 → 视为缺失（阻断）
MATRIX_EMPTY_EXP='[{"scenario":"s","phase":"immediate","consumer":"direct_query","expected":"","evidence":"t","status":"passed"}]'
make_ticket "$TMP/e2_empty_expected" "{
  \"ticket_id\":\"WF-AC-E2\",\"workflow_gate_schema_version\":1,
  \"impact_contract\":{\"identity_change\":true,\"completeness\":\"complete\"},
  \"acceptance_contract\":{\"requires_lifecycle\":true,\"matrix\":$MATRIX_EMPTY_EXP}
}"
if $LINT "$TMP/e2_empty_expected" --step deploy --json >/dev/null 2>&1; then
  bad "E2 空 expected cell 应视为缺失（阻断）"
else
  ok "E2 空 expected cell → 视为缺失阻断"
fi

echo ""
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
