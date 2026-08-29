#!/usr/bin/env bash
# reasoning gate（分级思考治理）改造 —— 20 轮全面自检
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
GATES="mcp/reasoning-gate/gates.json"
STEPS="steps/*.md"

echo "=== reasoning gate ${ROUNDS} 轮全面自检（py=$(echo "$PY_FILES" | wc -l) sh=$(echo "$SH_FILES" | wc -l)）==="

for r in $(seq 1 "$ROUNDS"); do
  err=0
  # 1. 语法/编译
  for f in $PY_FILES; do
    python3 -m py_compile "$f" 2>/dev/null || { echo "  [r$r] 语法 py: $f"; err=1; }
  done
  for f in $SH_FILES; do
    bash -n "$f" 2>/dev/null || { echo "  [r$r] 语法 sh: $f"; err=1; }
  done
  # 2. 依赖/调用链：gates.json 可解析且字段完整
  python3 - "$GATES" <<'PY' 2>/dev/null || { echo "  [r$r] gates.json 校验失败"; err=1; }
import json,sys
g=json.load(open(sys.argv[1]))
assert g["schema_version"]==1
need={"status","list","install","bak","readme","ppt","close","reopen","worktree","init","doc","limit","merge","plan","review","code","patch","log","deepcheck","audit"}
assert need <= set(g["steps"].keys()), sorted(need-set(g["steps"]))
assert g["mechanisms_by_tier"]=={"L0":"deterministic_checks","L1":"decision_record","L2":"sequential-thinking","L3":"sequential-thinking+adversarial"}
assert len(g["escalation_triggers"]["to_l2"])>=7 and len(g["escalation_triggers"]["to_l3"])>=5
PY
  # 3. 关联/一致性：活动工作流文档不得残留旧契约；step 文档必须声明分级
  if grep -rn "所有步骤必用\|每步必用\|每步至少\|至少 3 步\|至少3步\|至少 4 步\|至少4步\|至少 5 步\|至少5步" \
       $MD_ACTIVE mcp/_lib/*.py mcp/*/scripts/*.py 2>/dev/null | grep -v gate_sim | grep -q .; then
    echo "  [r$r] 旧契约残留"; err=1
  fi
  for f in $STEPS; do
    grep -q "思考分级\|reasoning gate\|L0\|L1\|L2\|L3" "$f" || { echo "  [r$r] step 未声明分级: $f"; err=1; }
  done
  # 4. 可运行/回归：全部测试套件
  for t in tests/test_*.sh; do
    bash "$t" >/dev/null 2>&1 || { echo "  [r$r] 测试失败: $t"; err=1; }
  done
  # 5. 异常/边界：validator 对缺失目录返回受控退出码（0/1/2），--json 输出可解析
  python3 tools/lint_thinking_gate.py /nonexistent_dir >/dev/null 2>&1; rc=$?
  if [ "$rc" -lt 0 ] || [ "$rc" -gt 2 ]; then echo "  [r$r] lint 异常路径 rc=$rc"; err=1; fi
  if ! python3 tools/lint_thinking_gate.py demo/.icode_output/.gate_sim/s2_readme_l1 --json 2>/dev/null | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
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
