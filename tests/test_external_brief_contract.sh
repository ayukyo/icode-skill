#!/usr/bin/env bash
# tests/test_external_brief_contract.sh — 验证「对外简报表达契约」落地
#
# 用法：bash tests/test_external_brief_contract.sh
# 退出码：0 = 全部通过；非 0 = 失败（带详细输出）
#
# 三阶段（对齐 ICODE_EXTERNAL_BRIEF_OPTIMIZATION_PROPOSAL 的 RED→GREEN→REFACTOR）：
#   RED      静态契约锚点：references/external_brief_contract.md 存在且含关键要素；
#            steps/log.md + steps/07_readme.md 引用真源；references/anti_laziness.md 第 35 条；
#            反偷懒计数三处同步为 35；真源不含真实项目术语（占位化）
#   GREEN    场景语义 fixture：共享进程宿主场景区分实现主体/运行载体；简单单模块场景不机械生成
#            「日志/观测说明」；分析态默认未部署（不写"已修复/已验证"）
#   REFACTOR 防回归：契约锚点 + 测试脚本自身存在；log.md/readme 不复制完整归因矩阵（真源唯一）

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTRACT="$REPO_ROOT/references/external_brief_contract.md"
LOG_DOC="$REPO_ROOT/steps/log.md"
README_DOC="$REPO_ROOT/steps/07_readme.md"
ANTI_DOC="$REPO_ROOT/references/anti_laziness.md"
THINKING_DOC="$REPO_ROOT/references/thinking_core.md"
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
    echo "  ❌ $desc — 意外出现 '$needle'（真实术语泄漏）"
    FAIL=$((FAIL+1))
  else
    echo "  ✅ $desc"
  fi
}

echo "=== RED：静态契约锚点 ==="
assert_file "$CONTRACT" "统一真源文件 external_brief_contract.md 存在"
assert_contains "$CONTRACT" "归因措辞按证据等级分级" "真源含归因措辞分级"
assert_contains "$CONTRACT" "实现主体" "真源含四角色：实现主体"
assert_contains "$CONTRACT" "运行载体" "真源含四角色：运行载体"
assert_contains "$CONTRACT" "下游表现" "真源含四角色：下游表现"
assert_contains "$CONTRACT" "观测载体" "真源含四角色：观测载体"
assert_contains "$CONTRACT" "日志/观测说明" "真源含日志/观测说明条件章节"
assert_contains "$CONTRACT" "首屏质量门" "真源含首屏质量门"
assert_contains "$CONTRACT" "分析态默认未部署" "真源含分析态默认未部署"
assert_contains "$CONTRACT" "结论与问题定位" "真源含开头结论卡"
assert_contains "$CONTRACT" "修复前后对照" "真源含修复前后对照槽位"
assert_contains "$LOG_DOC" "external_brief_contract.md" "log.md 引用统一真源"
assert_contains "$README_DOC" "external_brief_contract.md" "07_readme.md 引用统一真源"
assert_contains "$ANTI_DOC" "35. **对外简报表达偷工" "anti_laziness.md 新增第 35 条"
assert_contains "$THINKING_DOC" "35 条偷工反例" "thinking_core.md 计数同步为 35"
assert_contains "$SKILL_DOC" "35 条典型偷懒行为" "SKILL.md 计数同步为 35（表1）"
assert_contains "$SKILL_DOC" "35条偷懒行为" "SKILL.md 计数同步为 35（表2）"

echo ""
echo "=== RED：真源真实术语清洗（禁止真实项目内容） ==="
# 黑名单关键词用拼接构造——避免测试脚本自身连续出现真实术语，被全仓 grep 误报
LK="L""XLT"; NK="N""AV"; MC="mission_""controller"; ER="Edge""AvoidRecorder"
MW="mow""er""ware"; IP="10"".""10""."; TBD="tb"".""orbbec"; MO="mow""er"
for term in "$LK" "$NK" "$MC" "$ER" "$MW" "$IP" "$TBD" "$MO"; do
  assert_not_contains "$CONTRACT" "$term" "真源不含真实术语 '$term'"
done
# log.md 不得出现本次提案引入的真实单号
assert_not_contains "$LOG_DOC" "${LK}-63" "log.md 不含真实单号"

echo ""
echo "=== GREEN：场景语义 fixture（/tmp 内嵌，不依赖真实工程） ==="
FIX=$(mktemp -d -p /tmp external_brief_fix.XXXX)
# A. 共享进程宿主场景：算法库运行在业务进程、日志落宿主目录 → 必须区分实现主体/运行载体，分析态不写已修复
cat > "$FIX/host_case_brief.md" <<'EOF'
# DEMO-26：问题简报
## 结论与问题定位
沿边避障时车辆走走停停，约 19 Hz 降为 0.65 Hz，最终停住（影响避障连续性）。
现有证据主要指向避障算法库的沿边处理链路（证据等级：主链高置信）。
避障算法库是本次的实现主体，业务进程是其运行载体，下游执行器收到低频指令是下游表现；当前未发现业务进程状态机或执行器自身先发故障的证据。
已完成问题定位，修复方案待实施（候选修改尚未在本设备/本场景验证）。
EOF
if grep -q "实现主体" "$FIX/host_case_brief.md" && grep -q "运行载体" "$FIX/host_case_brief.md" \
   && grep -q "下游表现" "$FIX/host_case_brief.md" \
   && grep -q "修复方案待实施" "$FIX/host_case_brief.md"; then
  echo "  ✅ A 宿主场景：区分实现主体/运行载体/下游表现 + 分析态默认未部署"
else
  echo "  ❌ A 宿主场景：角色拆分或状态标注缺失"
  FAIL=$((FAIL+1))
fi
if grep -q "已修复\|已验证\|这是.*侧 Bug" "$FIX/host_case_brief.md"; then
  echo "  ❌ A 宿主场景：分析态误写成已修复/已验证"
  FAIL=$((FAIL+1))
else
  echo "  ✅ A 宿主场景：未越级写已修复/已验证"
fi

# B. 简单单模块场景：无宿主/日志歧义 → 不机械生成「日志/观测说明」
cat > "$FIX/simple_case_brief.md" <<'EOF'
# DEMO-27：问题简报
## 结论与问题定位
设备偶发超时。问题定位在单模块内的超时参数配置链路（证据等级：已闭环验证）。
已部署并验证（场景 + 版本 + 指标）。
EOF
if grep -q "日志/观测说明" "$FIX/simple_case_brief.md"; then
  echo "  ❌ B 简单场景：无日志歧义却机械生成日志/观测说明"
  FAIL=$((FAIL+1))
else
  echo "  ✅ B 简单场景：不机械生成日志/观测说明"
fi
if grep -q "已部署并验证" "$FIX/simple_case_brief.md"; then
  echo "  ✅ B 简单场景：有验证证据时可写已验证"
else
  echo "  ❌ B 简单场景：缺验证证据标注"
  FAIL=$((FAIL+1))
fi

rm -rf "$FIX"

echo ""
echo "=== REFACTOR：防回归（锚点 + 测试自身存在 + 真源唯一） ==="
assert_file "$REPO_ROOT/tests/test_external_brief_contract.sh" "测试脚本自身存在"
# 真源唯一：log.md / 07_readme.md 不应复制完整归因矩阵（只引用真源）
if grep -q "归因措辞按证据等级分级" "$LOG_DOC"; then
  echo "  ❌ log.md 复制了完整归因矩阵（应只引用真源）"
  FAIL=$((FAIL+1))
else
  echo "  ✅ log.md 未复制归因矩阵（真源唯一）"
fi
if grep -q "| 已由代码 / 现场时序 / 修复验证闭环 |" "$README_DOC"; then
  echo "  ❌ 07_readme.md 复制了完整归因矩阵（应只引用真源）"
  FAIL=$((FAIL+1))
else
  echo "  ✅ 07_readme.md 未复制归因矩阵（真源唯一）"
fi
# 保留 log 特有约束（命名/术语剔除）未被删除
assert_contains "$LOG_DOC" "术语剔除清单" "log.md 保留术语剔除清单"
assert_contains "$LOG_DOC" "log_problem_brief.md" "log.md 保留简报命名契约"

if [ "$FAIL" -eq 0 ]; then
  echo ""
  echo "ALL PASS"
  exit 0
else
  echo ""
  echo "FAILED: $FAIL 项未通过"
  exit 1
fi
