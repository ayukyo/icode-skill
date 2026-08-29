#!/usr/bin/env bash
# 契约测试：语义决策门禁（semantic decision gate）
#
# 覆盖 WORKFLOW_OPTIMIZATION_PROPOSAL.md §7.1-1 / §7.2-1：
#   负向：分析给出两个外部行为不同的选项但没有用户决策 → plan/code/patch 合并应失败（lint 阻断）
#   正向：用户已确认唯一策略 → 允许进入实现
#   边界：diagnosis-only 允许诊断结束但不得进入实现；resolved 缺 selected 视为不完整
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
LINT="python3 tools/lint_workflow_contract.py"

# ---- 静态契约 ----
check_contains() {  # $1=文件 $2=文本 $3=描述
  if grep -q "$2" "$1" 2>/dev/null; then ok "$3"; else bad "$3 ($1 缺: $2)"; fi
}
check_contains SKILL.md "semantic_decisions" "SKILL.md 定义 semantic_decisions 字段"
check_contains SKILL.md "lint_workflow_contract" "SKILL.md 引用 lint_workflow_contract.py"
check_contains SKILL.md "diagnosis_only" "SKILL.md 定义 diagnosis_only"
check_contains references/dir_and_metadata.md "semantic_decisions" "dir_and_metadata.md 定义 semantic_decisions 字段族"
check_contains references/dir_and_metadata.md "diagnosis_only" "dir_and_metadata.md 定义 diagnosis_only 字段族"
check_contains mcp/workflow-gate/gates.json "semantic_decision_gate" "gates.json 含 semantic_decision_gate"
check_contains steps/01_plan.md "semantic_decisions" "plan 步骤写 semantic_decisions"
check_contains steps/03_merge.md "lint_workflow_contract.py" "merge 步骤跑 workflow gate 校验"
check_contains steps/08_patch.md "lint_workflow_contract.py" "patch 步骤跑 workflow gate 校验"

# ---- 运行时 fixture ----
make_ticket() {  # $1=dir  $2=metadata_json
  mkdir -p "$1"
  printf '%s\n' "$2" > "$1/.ico_metadata.json"
}

# 负向：两个外部行为不同的选项，无用户决策 → 全量扫描 blocked
make_ticket "$TMP/n1_unresolved" '{
  "ticket_id":"WF-SEM-N1","workflow_gate_schema_version":1,
  "semantic_decisions":[{"dimension":"关系冲突处理","alternatives":["拒绝","收敛"],"selected":null,"user_confirmed":false,"status":"open"}]
}'
if $LINT "$TMP/n1_unresolved" --json >/dev/null 2>&1; then
  bad "N1 未解决语义决策应阻断（exit0 误放行）"
else
  ok "N1 未解决语义决策 → 阻断"
fi
# --step plan/code/patch 都应阻断
for st in plan code patch; do
  if $LINT "$TMP/n1_unresolved" --step "$st" --json >/dev/null 2>&1; then
    bad "N1 --step $st 应阻断"
  else
    ok "N1 --step $st 阻断"
  fi
done

# 负向：用户已确认唯一策略（resolved）→ 允许
make_ticket "$TMP/p1_resolved" '{
  "ticket_id":"WF-SEM-P1","workflow_gate_schema_version":1,
  "semantic_decisions":[{"dimension":"关系冲突处理","alternatives":["拒绝","收敛"],"selected":"收敛为一个实体","evidence":"user-confirmed","user_confirmed":true,"status":"resolved"}]
}'
if $LINT "$TMP/p1_resolved" --step code --json >/dev/null 2>&1; then
  ok "P1 用户已确认唯一策略 → 允许进入实现"
else
  bad "P1 已 resolved 决策不应阻断"
fi

# 边界：resolved 但缺 selected → 视为不完整（阻断）
make_ticket "$TMP/e1_no_selected" '{
  "ticket_id":"WF-SEM-E1","workflow_gate_schema_version":1,
  "semantic_decisions":[{"dimension":"关系冲突处理","alternatives":["拒绝","收敛"],"user_confirmed":true,"status":"resolved"}]
}'
if $LINT "$TMP/e1_no_selected" --step code --json >/dev/null 2>&1; then
  bad "E1 resolved 但缺 selected 应阻断（合同不完整）"
else
  ok "E1 resolved 缺 selected → 阻断"
fi

# 边界：diagnosis-only 允许诊断结束（全量扫描 pass + warning），但 --step plan/code/patch 仍阻断
make_ticket "$TMP/e2_diagnosis" '{
  "ticket_id":"WF-SEM-E2","workflow_gate_schema_version":1,"diagnosis_only":true,
  "semantic_decisions":[{"dimension":"冲突优先级","alternatives":["A","B"],"selected":null,"user_confirmed":false,"status":"open"}]
}'
OUT="$(python3 - "$TMP/e2_diagnosis" <<'PY' 2>/dev/null
import json,sys,subprocess
r=json.loads(subprocess.run(["python3","tools/lint_workflow_contract.py",sys.argv[1],"--json"],capture_output=True,text=True).stdout)
print(r["blocked"], "|".join(g["warnings"][0] if g["warnings"] else "" for g in r["gates"]))
PY
)"
if $LINT "$TMP/e2_diagnosis" --json >/dev/null 2>&1; then
  ok "E2 diagnosis-only 全量扫描允许结束"
else
  bad "E2 diagnosis-only 全量扫描不应阻断"
fi
if echo "$OUT" | grep -q "仅诊断"; then
  ok "E2 diagnosis-only 输出 warning「仅诊断」"
else
  bad "E2 diagnosis-only 未标记「仅诊断」"
fi
if $LINT "$TMP/e2_diagnosis" --step code --json >/dev/null 2>&1; then
  bad "E2 diagnosis-only 但 --step code 应仍阻断（不得进入实现）"
else
  ok "E2 diagnosis-only 不进入实现（--step code 阻断）"
fi

# 兼容：旧工单缺字段 → legacy-untracked 提示不阻断（非 strict）
make_ticket "$TMP/legacy" '{"ticket_id":"WF-SEM-OLD","status":"completed"}'
if $LINT "$TMP/legacy" --json >/dev/null 2>&1; then
  ok "旧工单缺字段 → legacy-untracked 提示不阻断"
else
  bad "旧工单缺字段不应阻断（提示模式）"
fi
if $LINT "$TMP/legacy" --strict --json >/dev/null 2>&1; then
  bad "旧工单 --strict 应阻断"
else
  ok "旧工单 --strict 强制模式阻断"
fi

echo ""
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
