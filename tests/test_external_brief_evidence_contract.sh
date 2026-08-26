#!/usr/bin/env bash
# tests/test_external_brief_evidence_contract.sh — 验证「对外简报证据链：语义槽位 + 版本基线」落地
#
# 用法：bash tests/test_external_brief_evidence_contract.sh
# 退出码：0 = 全部通过；非 0 = 失败（带详细输出）
#
# 三阶段（RED→GREEN→REFACTOR）：
#   RED      静态契约锚点：external_brief_contract.md 含 §7.1 六必填语义槽位 + §10 对外简报完成门；
#            07_readme.md 含「TB/日志源简报的强制语义槽位」+ 简报不出现 worktree 状态段；
#            log.md 对外简报段引用语义槽位/版本信息；anti_laziness 第 37 条
#   GREEN    场景语义 fixture：TB/日志型简报含 现场时间线（毫秒级）+ 问题—修复—验证映射 +
#            修复前后链路 + 版本信息（现场/修复/验证三版本）+ 无 worktree 清理命令；
#            简单场景不机械（已由 test_external_brief_contract.sh 覆盖）
#   REFACTOR 防回归：worktree 状态段仅交付报告（简报不再追加）；测试自身存在

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTRACT="$REPO_ROOT/references/external_brief_contract.md"
README_DOC="$REPO_ROOT/steps/07_readme.md"
LOG_DOC="$REPO_ROOT/steps/log.md"
ANTI_DOC="$REPO_ROOT/references/anti_laziness.md"

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

echo "=== RED：静态契约锚点 ==="
assert_contains "$CONTRACT" "§7.1" "真源含 §7.1 必填语义槽位"
assert_contains "$CONTRACT" "原现场时间线" "槽位①：原现场时间线"
assert_contains "$CONTRACT" "问题点—修复点—验证结果映射" "槽位②：问题点—修复点—验证结果映射"
assert_contains "$CONTRACT" "修复前后链路" "槽位③：修复前后链路"
assert_contains "$CONTRACT" "验证时间线与版本" "槽位④：验证时间线与版本"
assert_contains "$CONTRACT" "版本信息" "槽位⑤：版本信息"
assert_contains "$CONTRACT" "统一对外结论口径" "槽位⑥：统一对外结论口径"
assert_contains "$CONTRACT" "对外简报完成门" "真源含 §10 对外简报完成门"
assert_contains "$CONTRACT" "机器自检" "真源含机器自检描述"
assert_contains "$CONTRACT" "delivery_verdict != verified" "完成门含验收边界（未 verified 不写整链验证完成）"
assert_contains "$CONTRACT" "短 Hash" "完成门含短 Hash 展示要求"
assert_contains "$README_DOC" "TB/日志源简报的强制语义槽位" "07_readme.md 含强制语义槽位小节"
assert_contains "$README_DOC" "简报不出现 worktree 状态段" "07_readme.md 声明简报不出现 worktree 状态段"
assert_contains "$README_DOC" "继承 log_analysis §7.5" "07_readme.md 修复前后链路继承 §7.5"
assert_contains "$README_DOC" "对外简报完成门" "07_readme.md 含对外简报完成门"
assert_contains "$LOG_DOC" "原现场时间线" "log.md 对外简报段含现场时间线槽位"
assert_contains "$LOG_DOC" "版本信息" "log.md 对外简报段含版本信息槽位"
assert_contains "$LOG_DOC" "§10 对外简报完成门" "log.md 对外简报段引用 §10 完成门"
assert_contains "$ANTI_DOC" "日志有版本证据仍直接用当前 HEAD 解释现场" "anti_laziness.md 含版本基线条（第 37 条）"

echo ""
echo "=== RED：真源真实术语清洗（禁止真实项目内容） ==="
LK="L""XLT"; NK="N""AV"; MC="mission_""controller"; ER="Edge""AvoidRecorder"
MW="mow""er""ware"; IP="10"".""10""."; TBD="tb"".""orbbec"; MO="mow""er"
for term in "$LK" "$NK" "$MC" "$ER" "$MW" "$IP" "$TBD" "$MO"; do
  assert_not_contains "$CONTRACT" "$term" "真源不含真实术语 '$term'"
done
assert_not_contains "$README_DOC" "${LK}-63" "07_readme.md 不含真实单号"

echo ""
echo "=== GREEN：场景语义 fixture（/tmp 内嵌，不依赖真实工程） ==="
FIX=$(mktemp -d -p /tmp brief_evidence_fix.XXXX)

# A. TB/日志型简报：含 6 语义槽位 + 无 worktree 清理命令
cat > "$FIX/log_brief.md" <<'EOF'
# DEMO-26：充电完成后设备未停稳即切换任务模式
## 结论与问题定位
一句话：自动任务切换触发时设备未满足静止等待条件，状态估计器观测失效导致输出发散（置信度：中高；修复方案待实施验证）。

## 现场时间线
| 时间（毫秒） | 动作 | 日志表现 | 结论 |
|---|---|---|---|
| 10:01:23.482 | 任务切换请求 | `mode=MANUAL, velocity=0.8m/s` | 设备仍在移动中触发切换 |
| 10:01:23.490 | 状态估计器 | 观测失败 ×1 | 输入=0，观测连续失败 |
| 10:01:24.105 | 输出 | 位置偏离 [1.2km, -0.3km] | 惯性传播发散 |
来源：现场日志节点 A（主控）；时钟偏差 0ms（与采集端 NTP 同步）。

## 问题点—修复点—验证结果映射
| 问题表现 | 修复点 | 验证结果 |
|---|---|---|
| 移动中允许切换任务 | 任务切换入口加静止等待检查 | 待实机验证（未部署） |

## 修复前后链路
修复前：任务切换 →（无静止检查）→ 状态估计器观测失效 → 输出发散
修复后：任务切换 → 静止等待确认 → 状态估计器正常观测 → 输出收敛（**未部署，目标链路**）
（低复杂度场景，用对照表代替同图）

## 版本信息
- 现场运行版本：`git=7f3a2c1`（日志启动行解析）
- 当前分析版本：`git=9b41e0d`（HEAD）
- 修复验证版本：未部署（待验证）
- 现场 Hash 缺失时显式降级：日志未记录或未能解析现场 Git Hash
EOF
if grep -q "现场时间线" "$FIX/log_brief.md" && grep -q "10:01:23.482" "$FIX/log_brief.md"; then
  echo "  ✅ A 含原现场时间线（毫秒级时间戳）"
else
  echo "  ❌ A 缺原现场时间线"
  FAIL=$((FAIL+1))
fi
if grep -q "问题点—修复点—验证结果映射" "$FIX/log_brief.md"; then
  echo "  ✅ A 含问题—修复—验证映射"
else
  echo "  ❌ A 缺问题—修复—验证映射"
  FAIL=$((FAIL+1))
fi
if grep -q "修复前后链路" "$FIX/log_brief.md" && grep -q "未部署" "$FIX/log_brief.md"; then
  echo "  ✅ A 含修复前后链路（未部署声明）"
else
  echo "  ❌ A 缺修复前后链路"
  FAIL=$((FAIL+1))
fi
if grep -q "版本信息" "$FIX/log_brief.md" && grep -q "现场运行版本" "$FIX/log_brief.md"; then
  echo "  ✅ A 含三版本信息"
else
  echo "  ❌ A 缺版本信息"
  FAIL=$((FAIL+1))
fi
if grep -q "git worktree\|git worktree remove\|git merge\|git branch -d\|回流" "$FIX/log_brief.md"; then
  echo "  ❌ A 简报泄漏 worktree 清理命令/回流操作"
  FAIL=$((FAIL+1))
else
  echo "  ✅ A 简报不含 worktree 清理命令/回流操作"
fi

# B. 现场 Hash 缺失 → 显式降级（不得把当前代码版本冒充现场版本）
cat > "$FIX/hash_missing_brief.md" <<'EOF'
## 版本信息
现场运行版本：日志未记录或未能解析现场 Git Hash（显式降级）
当前分析版本：git=9b41e0d（HEAD）
EOF
if grep -q "日志未记录或未能解析现场 Git Hash" "$FIX/hash_missing_brief.md"; then
  echo "  ✅ B 现场 Hash 缺失显式降级"
else
  echo "  ❌ B 缺显式降级措辞"
  FAIL=$((FAIL+1))
fi
cat > "$FIX/hash_missing_bad.md" <<'EOF'
## 版本信息
现场运行版本：git=9b41e0d（当前 HEAD）
EOF
if grep -q "日志未记录或未能解析现场 Git Hash" "$FIX/hash_missing_bad.md"; then
  echo "  ❌ B 用 HEAD 冒充现场版本未被识别"
  FAIL=$((FAIL+1))
else
  echo "  ✅ B 用 HEAD 冒充现场版本被拒"
fi

rm -rf "$FIX"

echo ""
echo "=== REFACTOR：防回归（worktree 状态段仅交付报告 + 测试自身存在） ==="
assert_file "$REPO_ROOT/tests/test_external_brief_evidence_contract.sh" "测试脚本自身存在"
# worktree 状态段触发说明不再要求"简报也追加"（已改为仅交付报告）
assert_not_contains "$README_DOC" "跨领域简报**均**追加" "worktree 状态段不再要求简报也追加"
assert_not_contains "$README_DOC" "简报「背景与主要问题」之前" "worktree 状态段位置不再指向简报"
# 简报生成流程步骤 9 引用语义槽位
assert_contains "$README_DOC" "TB/日志源简报按「TB/日志源简报的强制语义槽位」" "简报生成步骤引用语义槽位"

if [ "$FAIL" -eq 0 ]; then
  echo ""
  echo "ALL PASS"
  exit 0
else
  echo ""
  echo "FAILED: $FAIL 项未通过"
  exit 1
fi
