#!/usr/bin/env bash
# workflow contract（工作流硬门禁 P0×4 + P1 生命周期验收）—— 20 轮全面自检
# 每轮覆盖 7 维：语法/编译、依赖/调用链、逻辑/边界、异常/边界、关联/一致性、兼容/回归、可运行。
# 全部 20 轮必须 PASS；任一轮失败即整体失败（自检不通过不交付）。
set -u
cd "$(dirname "$0")/.." || exit 1

ROUNDS="${1:-20}"
FAILED=0
# 只对相关源码做语法编译（排除 .venv / node_modules / __pycache__ / demo 产物）
PY_FILES="$(find mcp tools scripts tests -name '*.py' -not -path '*/.venv/*' -not -path '*/node_modules/*' -not -path '*/__pycache__/*' | sort)"
SH_FILES="$(find . -name '*.sh' -not -path '*/.venv/*' -not -path '*/node_modules/*' -not -path '*/.icode_output/*' | sort)"
MD_ACTIVE="SKILL.md README.md README.zh-CN.md references/*.md steps/*.md mcp/*/README.md"
GATES="mcp/workflow-gate/gates.json"
STEPS="steps/*.md"
LINT_PY="tools/lint_workflow_contract.py"
LINT="python3 $LINT_PY"
SIM="demo/.icode_output/.workflow_sim"

echo "=== workflow contract ${ROUNDS} 轮全面自检（py=$(echo "$PY_FILES" | wc -l) sh=$(echo "$SH_FILES" | wc -l)）==="

for r in $(seq 1 "$ROUNDS"); do
  err=0
  # 1. 语法/编译
  for f in $PY_FILES; do
    python3 -m py_compile "$f" 2>/dev/null || { echo "  [r$r] 语法 py: $f"; err=1; }
  done
  for f in $SH_FILES; do
    bash -n "$f" 2>/dev/null || { echo "  [r$r] 语法 sh: $f"; err=1; }
  done
  # 2. 依赖/调用链：gates.json 可解析且字段完整 + lint 步骤映射覆盖门禁入口
  python3 - "$GATES" "$LINT_PY" <<'PY' 2>/dev/null || { echo "  [r$r] gates.json/lint 校验失败"; err=1; }
import json,re,sys
g=json.load(open(sys.argv[1]))
assert g["schema_version"]==1
assert "semantic_decision_gate" in g and "identity_change_gate" in g
assert "requirement_delta_escalation" in g and "fast_risk_upgrade" in g and "acceptance_contract" in g
assert len(g["constants"]["impact_checklist_dimensions"])==9
assert len(g["constants"]["acceptance_phases"])==4
assert len(g["constants"]["acceptance_consumers"])==4
assert len(g["constants"]["fast_risk_triggers"])>=9
# 步骤映射必须覆盖所有门禁入口步骤（lint 内 STEP_GATES）
src=open(sys.argv[2]).read()
m=re.search(r"STEP_GATES\s*=\s*\{.*?\}", src, re.S)
assert m, "lint 缺少 STEP_GATES"
for step in ("plan","code","patch","merge","deploy","audit-verified","fast"):
    assert step in m.group(0), f"STEP_GATES 缺 {step}"
PY
  # 3. 关联/一致性：workflow gate 相关 step 文档必须挂接；SKILL 计数声明一致
  for f in steps/00_init.md steps/01_plan.md steps/02_review.md steps/03_merge.md \
           steps/04_code.md steps/05_deepcheck.md steps/06_audit.md steps/08_patch.md steps/fast.md steps/log.md; do
    grep -qi "workflow gate\|workflow_gate\|工作流硬门禁\|语义决策\|identity_change\|acceptance_contract\|生命周期" "$f" \
      || { echo "  [r$r] workflow step 未挂接 gate: $f"; err=1; }
  done
  if ! grep -q "anti_laziness.*39\|39 条\|39 项" SKILL.md; then
    echo "  [r$r] SKILL.md anti_laziness 计数未同步（应为 39）"; err=1
  fi
  # 4. 可运行/回归：全部测试套件
  for t in tests/test_*.sh; do
    bash "$t" >/dev/null 2>&1 || { echo "  [r$r] 测试失败: $t"; err=1; }
  done
  # 5. 异常/边界：validator 对缺失目录返回受控退出码（0/1/2），--json 输出可解析
  $LINT /nonexistent_dir >/dev/null 2>&1; rc=$?
  if [ "$rc" -lt 0 ] || [ "$rc" -gt 2 ]; then echo "  [r$r] lint 异常路径 rc=$rc"; err=1; fi
  if ! $LINT "$SIM/s2_unresolved" --json 2>/dev/null | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
    echo "  [r$r] lint --json 输出不可解析"; err=1
  fi

  if [ "$err" -eq 0 ]; then
    echo "  [r$r] ✅ 7 维自检全过"
  else
    echo "  [r$r] ❌ 本轮失败 ($err)"
    FAILED=1
  fi
done

echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "✅ 全部 $ROUNDS 轮通过 —— 语法/依赖/逻辑/异常/关联/兼容/可运行 7 维均无问题"
  exit 0
else
  echo "❌ 存在失败轮次，自检不通过，禁止交付"
  exit 1
fi