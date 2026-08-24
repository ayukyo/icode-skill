#!/usr/bin/env bash
# tests/test_tdd_workflow_contract.sh — 验证「TDD 工作流契约」落地
#
# 用法：bash tests/test_tdd_workflow_contract.sh
# 退出码：0 = 全部通过；非 0 = 失败（带详细输出）
#
# 契约（对齐 ICODE_TDD_WORKFLOW_OPTIMIZATION_PROPOSAL §11）：
#   1. 01_plan.md 含 TDD 测试契约（每个 A 档行为变化的测试契约表）
#   2. 04_code.md 明确 RED 必须发生在生产代码 Edit 前
#   3. 04_code.md 区分预期断言失败与测试基础设施错误
#   4. 04_code.md 定义"测试首次即通过则停止重审"分支
#   5. fast.md 明确 fast 不跳过 RED→GREEN 门禁
#   6. 08_patch.md 代码修改型 patch 执行轻量 RED→GREEN
#   7. 05_deepcheck.md 和 06_audit.md 消费 tdd 证据
#   8. SKILL.md 中 tdd metadata 字段与默认值（not_assessed）定义一致
#   9. 主流程步骤编号及 completed_steps 合法值未因本改造增加
#  10. O-6 无有效 RED 时不把 delivery_verdict 标 verified（verification_pending）

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLAN_DOC="$REPO_ROOT/steps/01_plan.md"
CODE_DOC="$REPO_ROOT/steps/04_code.md"
FAST_DOC="$REPO_ROOT/steps/fast.md"
PATCH_DOC="$REPO_ROOT/steps/08_patch.md"
DEEPCHECK_DOC="$REPO_ROOT/steps/05_deepcheck.md"
AUDIT_DOC="$REPO_ROOT/steps/06_audit.md"
SKILL_DOC="$REPO_ROOT/SKILL.md"

FAIL=0

assert_contains() {
  local file="$1" needle="$2" desc="$3"
  if grep -qF -- "$needle" "$file"; then
    echo "  ✅ $desc"
  else
    echo "  ❌ $desc — 在 $file 未找到 '$needle'"
    FAIL=$((FAIL+1))
  fi
}

assert_file() {
  local file="$1" desc="$2"
  if [ -f "$file" ]; then
    echo "  ✅ $desc"
  else
    echo "  ❌ $desc — 文件不存在: $file"
    FAIL=$((FAIL+1))
  fi
}

assert_not_contains() {
  local file="$1" needle="$2" desc="$3"
  if grep -qF -- "$needle" "$file"; then
    echo "  ❌ $desc — 意外出现 '$needle'"
    FAIL=$((FAIL+1))
  else
    echo "  ✅ $desc"
  fi
}

echo "=== 1. 01_plan.md：TDD 测试契约表 ==="
assert_contains "$PLAN_DOC" "TDD 测试契约" "01_plan 含 TDD 测试契约锚点"
assert_contains "$PLAN_DOC" "RED 预期" "01_plan 契约含 RED 预期（修复前为何失败）"

echo ""
echo "=== 2. 04_code.md：RED 在生产代码 Edit 前 ==="
assert_contains "$CODE_DOC" "TDD 准入门" "04_code 含 TDD 准入门"
assert_contains "$CODE_DOC" "有效 RED" "04_code 定义有效 RED 判定"
assert_contains "$CODE_DOC" "生产代码" "04_code 提及生产代码"
if grep -q "取得有效 RED.*Edit 生产代码\|RED.*修改生产代码\|有效 RED.*才允许" "$CODE_DOC"; then
  echo "  ✅ 04_code 明确 RED 在生产代码 Edit 前"
else
  echo "  ❌ 04_code 未明确 RED 在 Edit 前的硬顺序"
  FAIL=$((FAIL+1))
fi

echo ""
echo "=== 3. 04_code.md：失败分类（区分预期断言 vs 基础设施错误） ==="
assert_contains "$CODE_DOC" "expected_assertion" "04_code 含 expected_assertion 失败分类"
assert_contains "$CODE_DOC" "harness_compile_error" "04_code 含 harness_compile_error"
assert_contains "$CODE_DOC" "environment_error" "04_code 含 environment_error（环境错误不算 RED）"
if grep -q "expected_assertion.*red_verified\|只有.*expected_assertion" "$CODE_DOC"; then
  echo "  ✅ 04_code 只有 expected_assertion 可推进 red_verified"
else
  echo "  ❌ 04_code 未限定只有 expected_assertion 算有效 RED"
  FAIL=$((FAIL+1))
fi

echo ""
echo "=== 4. 04_code.md：测试首次即通过停止分支 ==="
assert_contains "$CODE_DOC" "首次即通过" "04_code 含测试首次即通过分支"
if grep -q "首次即通过.*停止\|首次即通过.*重新做必要性" "$CODE_DOC"; then
  echo "  ✅ 04_code 首次通过 → 停止并重审"
else
  echo "  ❌ 04_code 首次通过缺少停止/重审分支"
  FAIL=$((FAIL+1))
fi

echo ""
echo "=== 5. fast.md：不跳过 RED→GREEN ==="
assert_contains "$FAST_DOC" "RED→GREEN" "fast 明确不省略 RED→GREEN"

echo ""
echo "=== 6. 08_patch.md：代码修改型 patch 轻量 RED→GREEN ==="
assert_contains "$PATCH_DOC" "RED" "08_patch 代码修改型含 RED"
assert_contains "$PATCH_DOC" "测试 Edit" "08_patch 含测试 Edit 先行（RED 顺序）"

echo ""
echo "=== 7. 05_deepcheck / 06_audit：消费 tdd 证据 ==="
assert_contains "$DEEPCHECK_DOC" "tdd" "05_deepcheck 消费 tdd 证据"
assert_contains "$AUDIT_DOC" "tdd" "06_audit 消费 tdd 证据"
assert_contains "$AUDIT_DOC" "有效 RED" "06_audit 以有效 RED 为 verified 前提"

echo ""
echo "=== 8. SKILL.md：tdd metadata 字段与默认值 ==="
assert_contains "$SKILL_DOC" "tdd" "SKILL 定义 tdd metadata 对象"
assert_contains "$SKILL_DOC" "not_assessed" "SKILL 定义 tdd.status 默认 not_assessed（旧工单兼容）"

echo ""
echo "=== 9. 主流程编号 / completed_steps 合法值未增加 ==="
if ls "$REPO_ROOT"/steps/04_test*.md "$REPO_ROOT"/steps/04_tdd*.md >/dev/null 2>&1; then
  echo "  ❌ steps/ 目录出现了 04_test/04_tdd 步骤文件（主流程编号被新增）"
  FAIL=$((FAIL+1))
else
  echo "  ✅ steps/ 目录无新增 04_test/04_tdd 步骤文件（主流程编号未增加）"
fi
# SKILL.md 的 04_test 仅允许以否定声明形式存在（"不存在 04_test"），不得作为合法步骤
if grep -q "不存在 \`03_code\` / \`04_test\`" "$SKILL_DOC"; then
  echo "  ✅ SKILL 以否定声明明确 04_test 非合法步骤（未新增）"
else
  echo "  ❌ SKILL 缺少 '04_test 非主步骤' 的否定声明"
  FAIL=$((FAIL+1))
fi

echo ""
echo "=== 10. O-6 / 无有效 RED 不标 verified ==="
assert_contains "$AUDIT_DOC" "verification_pending" "06_audit 无有效 RED → verification_pending（不写 verified）"
assert_contains "$AUDIT_DOC" "待实机验证" "06_audit 交付措辞保持 待实机验证"

if [ "$FAIL" -eq 0 ]; then
  echo ""
  echo "ALL PASS"
  exit 0
else
  echo ""
  echo "FAILED: $FAIL 项未通过"
  exit 1
fi
