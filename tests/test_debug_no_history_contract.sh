#!/usr/bin/env bash
# 契约测试：debug 工单不参考历史工单（独立孪生对照）
#
# 背景：debug 工单是"独立孪生对照"——它应被参考（产物供正常工单并列对照研读），
# 不去参考历史工单。若 debug 分析参考 index.json 里的历史正式工单结论，
# 会被既有结论带偏、无法独立形成自己的思考（正常 vs debug 两份对照收敛到同一历史结论）。
#
# 验证锚点（四处同步）：
#   steps/log.md 步骤2  --debug 分支（跳过源1·历史工单检索，保留段零+limit）
#   steps/00_init.md 步骤2  --debug 分支（同 log）
#   SKILL.md 历史检索复用段 debug 例外
#   tools/tb/scripts/tb_watch.py PROMPT_TMPL（debug 语义：跳过历史工单检索）
#   references/debug_mode.md §14（核心契约：debug 不参考历史工单）
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  ❌ $1"; }
check_contains() {  # $1=文件 $2=文本 $3=描述
  if grep -q "$2" "$1" 2>/dev/null; then ok "$3"; else bad "$3 ($1 缺: $2)"; fi
}

echo "=== 1. debug 跳过历史工单检索（log.md 步骤2） ==="
check_contains steps/log.md "debug 分支" "log.md 步骤2 有 debug 分支"
check_contains steps/log.md "跳过源1" "log.md 跳过源1"
check_contains steps/log.md "整段跳过" "log.md 明确整段跳过源1"
check_contains steps/log.md "保留.*段零工程文档检索" "log.md 保留段零"
check_contains steps/log.md "debug 模式跳过历史工单检索，无历史工单参考，仅注入段零工程文档" "log.md 思考块标注"

echo "=== 2. debug 跳过历史工单检索（00_init.md 步骤2） ==="
check_contains steps/00_init.md "debug 分支" "00_init.md 步骤2 有 debug 分支"
check_contains steps/00_init.md "整段跳过" "00_init.md 明确整段跳过源1"

echo "=== 3. SKILL.md 历史检索复用段 debug 例外 ==="
check_contains SKILL.md "debug 例外（独立孪生不参考历史工单）" "SKILL.md 有 debug 例外"
check_contains SKILL.md "跳过源1·历史工单检索" "SKILL.md 跳过源1"

echo "=== 4. tb_watch.py PROMPT（debug 语义） ==="
check_contains tools/tb/scripts/tb_watch.py "跳过历史工单检索（debug 独立孪生对照" "tb_watch PROMPT 跳过历史工单检索"

echo "=== 5. debug_mode.md §14 核心契约 ==="
check_contains references/debug_mode.md "## 14. debug 工单不参考历史工单（独立形成自己的思考）" "debug_mode.md §14 存在"
check_contains references/debug_mode.md "跳过历史工单检索" "§14 跳过历史工单检索"
check_contains references/debug_mode.md "参考自己" "§14 保留 debug 域旧孪生复用（参考自己）"

echo ""
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
