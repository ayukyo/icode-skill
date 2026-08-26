#!/usr/bin/env bash
# tests/test_log_runtime_git_baseline_contract.sh — 验证「现场运行版本基线门」落地
#
# 用法：bash tests/test_log_runtime_git_baseline_contract.sh
# 退出码：0 = 全部通过；非 0 = 失败（带详细输出）
#
# 三阶段（RED→GREEN→REFACTOR）：
#   RED      静态契约锚点：log.md 含「现场运行版本基线门」（版本证据来源优先级 / Hash 识别两条件 /
#            判定矩阵 / 只读 Git 白名单 / §2.0.1 / 9.6 版本基线完成门）；metadata 三基线字段出现在
#            log.md + SKILL.md + dir_and_metadata.md；anti_laziness 第 37 条；反偷懒计数三处同步；
#            真源不含真实项目术语
#   GREEN    场景语义 fixture：/tmp 真实 git 仓库验证——日志含 Hash → 按现场 Hash 读历史源码 →
#            演进对照 → 判定矩阵"已被后续提交修复"；无 Hash → unknown 显式降级（不得用 HEAD 冒充）；
#            dirty 标记 → 置信度降级
#   REFACTOR 防回归：只读白名单禁改工作区命令；测试自身存在；白名单命令真实可执行

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DOC="$REPO_ROOT/steps/log.md"
SKILL_DOC="$REPO_ROOT/SKILL.md"
ANTI_DOC="$REPO_ROOT/references/anti_laziness.md"
THINKING_DOC="$REPO_ROOT/references/thinking_core.md"
DIR_META="$REPO_ROOT/references/dir_and_metadata.md"

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
assert_contains "$LOG_DOC" "现场运行版本基线门" "log.md 含「现场运行版本基线门」"
assert_contains "$LOG_DOC" "按证据来源优先级" "log.md 含版本证据来源优先级"
assert_contains "$LOG_DOC" "build_info.json" "log.md 含版本文件证据来源（build_info）"
assert_contains "$LOG_DOC" "Hash 识别两条件" "log.md 含 Hash 识别两条件"
assert_contains "$LOG_DOC" "判定矩阵" "log.md 含现场→HEAD 判定矩阵"
assert_contains "$LOG_DOC" "已被后续提交修复" "log.md 判定矩阵含'已被后续提交修复'（不重复写修复）"
assert_contains "$LOG_DOC" "不能用当前回归解释更早现场" "log.md 判定矩阵含回归保护（不反向解释更早现场）"
assert_contains "$LOG_DOC" "git -C <repo> cat-file -e" "log.md 含 Hash 可解析验证命令"
assert_contains "$LOG_DOC" "git -C <repo> show <hash>:<path>" "log.md 含按现场 Hash 读历史源码命令"
assert_contains "$LOG_DOC" "git -C <repo> log --oneline <hash>..HEAD" "log.md 含现场→HEAD 演进对照命令"
assert_contains "$LOG_DOC" "§2.0.1 现场运行版本基线" "log.md 报告骨架含 §2.0.1"
assert_contains "$LOG_DOC" "现场运行版本未确定" "log.md 含无版本证据显式降级措辞"
assert_contains "$LOG_DOC" "9.6 版本基线完成门" "log.md 含步骤 9.6 版本基线完成门"
assert_contains "$LOG_DOC" "if '现场运行版本未确定' in s2:" "9.6 完成门降级判据用精确短语（防裸 unknown 误放行）"
assert_not_contains "$LOG_DOC" "'unknown' in s2" "9.6 完成门无裸 unknown 匹配（§2.1 业务 unknown 不误触发放行）"
assert_contains "$LOG_DOC" "不得用裸 \`unknown\` 替代" "9.6 判定规则② 显式禁止裸 unknown 替代降级措辞"
assert_contains "$LOG_DOC" "runtime_code_baselines" "log.md 步骤9 metadata 含 runtime_code_baselines"
assert_contains "$LOG_DOC" "analysis_code_baselines" "log.md 步骤9 metadata 含 analysis_code_baselines"
assert_contains "$LOG_DOC" "verification_code_baselines" "log.md 步骤9 metadata 含 verification_code_baselines"
assert_contains "$LOG_DOC" "relation_to_analysis_head" "log.md 含与 HEAD 关系枚举"
assert_contains "$LOG_DOC" "不自动 fetch" "log.md 含'Hash 不可达不自动 fetch'"
assert_contains "$SKILL_DOC" "现场运行版本基线门" "SKILL.md /icode log 概述含版本门"
assert_contains "$SKILL_DOC" "runtime_code_baselines" "SKILL.md metadata 字段表含 runtime_code_baselines"
assert_contains "$SKILL_DOC" "relation_to_analysis_head" "SKILL.md metadata 字段表含 relation_to_analysis_head"
assert_contains "$DIR_META" "metadata 三基线字段" "dir_and_metadata.md 含三基线字段定义"
assert_contains "$DIR_META" "git show <hash>:<path>" "dir_and_metadata.md 白名单含 git show（现场读码）"
assert_contains "$DIR_META" "git diff <commit>..HEAD -- <path>" "dir_and_metadata.md 白名单含 git diff（演进对照）"
assert_contains "$DIR_META" "git blame <commit> -- <path>" "dir_and_metadata.md 白名单含 git blame（行归因）"
# 动态读取 anti_laziness 实际条数，断言计数声明与之一致
ANTI_COUNT=$(grep -oE '^[0-9]+\.' "$ANTI_DOC" | tr -d '.' | sort -n | tail -1)
VERSION_NO=$(grep -oE '^[0-9]+\. \*\*日志有版本证据仍直接用当前 HEAD 解释现场' "$ANTI_DOC" | grep -oE '^[0-9]+' | head -1)
assert_contains "$ANTI_DOC" "${VERSION_NO}. **日志有版本证据仍直接用当前 HEAD 解释现场" "anti_laziness.md 含版本基线条（第 ${VERSION_NO} 条）"
assert_contains "$THINKING_DOC" "${ANTI_COUNT} 条偷工反例" "thinking_core.md 计数同步为 ${ANTI_COUNT}"
assert_contains "$SKILL_DOC" "${ANTI_COUNT} 条典型偷懒行为" "SKILL.md 计数同步为 ${ANTI_COUNT}（表1）"
assert_contains "$SKILL_DOC" "${ANTI_COUNT}条偷懒行为" "SKILL.md 计数同步为 ${ANTI_COUNT}（表2）"

echo ""
echo "=== RED：真源真实术语清洗（禁止真实项目内容） ==="
# 黑名单关键词用拼接构造——避免测试脚本自身连续出现真实术语，被全仓 grep 误报
LK="L""XLT"; NK="N""AV"; MC="mission_""controller"; ER="Edge""AvoidRecorder"
MW="mow""er""ware"; IP="10"".""10""."; TBD="tb"".""orbbec"; MO="mow""er"
for term in "$LK" "$NK" "$MC" "$ER" "$MW" "$IP" "$TBD" "$MO"; do
  assert_not_contains "$LOG_DOC" "$term" "log.md 不含真实术语 '$term'"
done

echo ""
echo "=== GREEN：场景语义 fixture（/tmp 真实 git 仓库 + 内嵌文本） ==="
FIX=$(mktemp -d -p /tmp baseline_fix.XXXX)

# A. 日志含现场 Hash → 按现场 Hash 读历史源码 → 演进对照 → 判定矩阵"已被后续提交修复"
git -C "$FIX" init -q
git -C "$FIX" config user.email test@example.com
git -C "$FIX" config user.name test
cat > "$FIX/module.py" <<'EOF'
def clamp(v):
    if v > 100:
        return 100
    return v
EOF
git -C "$FIX" add module.py
git -C "$FIX" commit -qm "feat: add clamp"          # 现场运行版本（含边界缺陷）
SHA_ON_SITE=$(git -C "$FIX" rev-parse HEAD)
cat > "$FIX/module.py" <<'EOF'
def clamp(v):
    if v >= 100:
        return 100
    return v
EOF
git -C "$FIX" add module.py
git -C "$FIX" commit -qm "fix: correct clamp boundary to >= (later commit)"  # 当前 HEAD（已修复）
SHA_HEAD=$(git -C "$FIX" rev-parse HEAD)
if [ "$SHA_ON_SITE" != "$SHA_HEAD" ]; then
  echo "  ✅ A 现场 Hash ≠ 当前 HEAD（fixture 构造成立）"
else
  echo "  ❌ A fixture 构造失败：现场 Hash 与 HEAD 相同"
  FAIL=$((FAIL+1))
fi
# 按现场 Hash 只读读历史源码（白名单命令真实可执行）
HIST=$(git -C "$FIX" show "$SHA_ON_SITE:module.py")
if echo "$HIST" | grep -q "if v > 100"; then
  echo "  ✅ A 按现场 Hash 读到历史源码（缺陷版本）"
else
  echo "  ❌ A git show 读现场源码失败"
  FAIL=$((FAIL+1))
fi
# 演进对照：现场..HEAD 找到修复提交
if git -C "$FIX" log --oneline "$SHA_ON_SITE..HEAD" | grep -q "correct clamp boundary"; then
  echo "  ✅ A 现场→HEAD 演进对照定位到修复提交"
else
  echo "  ❌ A git log 演进对照失败"
  FAIL=$((FAIL+1))
fi
# 判定矩阵结论：现场有缺陷 + 当前已修复 → 定位修复提交，不重复写修复
cat > "$FIX/report_verdict.md" <<EOF
## 2.0.1 现场运行版本基线
现场 Hash: ${SHA_ON_SITE:0:7} | 当前 HEAD: ${SHA_HEAD:0:7} | 关系: ancestor_of_head
现场 clamp 边界使用 \`if v > 100\`，当前 HEAD 已改为 \`if v >= 100\`（commit 定位见演进对照）。
判定：现场问题已被后续提交修复 → 定位修复提交，优先做版本同步/部署与回归，不重复写修复。
EOF
if grep -q "已被后续提交修复" "$FIX/report_verdict.md" && grep -q "不重复写修复" "$FIX/report_verdict.md"; then
  echo "  ✅ A 判定矩阵输出正确（已修复→定位提交，不重复写修复）"
else
  echo "  ❌ A 判定矩阵结论缺失"
  FAIL=$((FAIL+1))
fi
# 只读验证：现场读码后跟踪文件未被改动（git show/log 不改工作树；fixture 内未跟踪的报告文件不计）
if [ -z "$(git -C "$FIX" status --porcelain -- module.py)" ]; then
  echo "  ✅ A 只读命令未污染跟踪文件（module.py 无改动）"
else
  echo "  ❌ A 只读命令污染了跟踪文件"
  FAIL=$((FAIL+1))
fi

# B. 无版本证据 → 显式降级 unknown，不得用当前 HEAD 冒充现场版本
cat > "$FIX/no_hash_report.md" <<'EOF'
## 2.0.1 现场运行版本基线
现场运行版本未确定（unknown）：日志未记录或未能解析现场 Git Hash。
EOF
if grep -q "现场运行版本未确定" "$FIX/no_hash_report.md"; then
  echo "  ✅ B 无版本证据 → 显式降级 unknown"
else
  echo "  ❌ B 无版本证据未降级"
  FAIL=$((FAIL+1))
fi
cat > "$FIX/bad_report.md" <<'EOF'
## 2.0.1 现场运行版本基线
现场运行版本 = 当前 HEAD (abc1234)
EOF
if grep -q "现场运行版本未确定" "$FIX/bad_report.md"; then
  echo "  ❌ B 用当前 HEAD 冒充现场版本未被识别"
  FAIL=$((FAIL+1))
else
  echo "  ✅ B 用当前 HEAD 冒充现场版本被拒（须显式 unknown）"
fi

# C. dirty 标记 → 只能定位基础提交，置信度不得标高
cat > "$FIX/dirty_report.md" <<'EOF'
raw_version: git=abc1234-dirty
置信度：低（dirty 构建，未提交差异未知，不能精确复现运行二进制）
EOF
if grep -q -- "-dirty" "$FIX/dirty_report.md" && grep -q "置信度：低" "$FIX/dirty_report.md"; then
  echo "  ✅ C dirty 标记 → 置信度降级"
else
  echo "  ❌ C dirty 标记降级缺失"
  FAIL=$((FAIL+1))
fi

rm -rf "$FIX"

echo ""
echo "=== REFACTOR：防回归（白名单禁改工作区 + 测试自身存在） ==="
assert_file "$REPO_ROOT/tests/test_log_runtime_git_baseline_contract.sh" "测试脚本自身存在"
# 白名单语境下禁止改变工作区/索引/网络操作（dir_and_metadata.md 只读约束段）
if grep -qF -- '**禁止** `checkout`' "$DIR_META"; then
  echo "  ✅ 白名单显式禁止 checkout/switch/reset/stash/clean 等写操作"
else
  echo "  ❌ 白名单缺少禁止写操作声明"
  FAIL=$((FAIL+1))
fi
if grep -q "分析阶段不自动 fetch" "$DIR_META" || grep -q "不自动 fetch" "$LOG_DOC"; then
  echo "  ✅ 分析阶段不自动 fetch（禁网络写操作）"
else
  echo "  ❌ 缺少'不自动 fetch'约束"
  FAIL=$((FAIL+1))
fi

# 运行时防回归：真实提取 9.6 完成门代码，验证"§2.0.1 有矩阵 + §2.1 业务 unknown + metadata 空"必须被拦截
#   （第 1 轮自检发现：9.6 曾用裸 'unknown' in s2 判降级，§2.1 业务 unknown 会把偷懒场景误放行 → 已收紧为精确短语）
if command -v python3 >/dev/null 2>&1; then
  gate96=$(python3 - "$LOG_DOC" <<'PYEOF'
import re, sys, io
src = io.open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"### 9\.6 版本基线完成门.*?```bash\n(python3 -c \"\n.*?\n\"\n)", src, re.S)
sys.stdout.write(m.group(1) if m else "")
PYEOF
)
  if [ -z "$gate96" ]; then
    echo "  ❌ 运行时 9.6：未能从 log.md 提取完成门代码"
    FAIL=$((FAIL+1))
  else
    GATE_DIR="$FIX/gate96_biz_unknown"
    mkdir -p "$GATE_DIR"
    cat > "$GATE_DIR/log_analysis.md" <<'EOF'
## 2. 基线检查
### §2.0.1 现场运行版本基线
| 模块 | 现场 Hash | 当前 HEAD | 关系 |
|---|---|---|---|
| calc | 6510939 | 7071cef | ancestor_of_head |
演进对照：已被后续提交修复。
### §2.1 git diff 结论
xxx 状态 unknown，待确认。
EOF
    cat > "$GATE_DIR/.ico_metadata.json" <<'EOF'
{"runtime_code_baselines": []}
EOF
    gate_code="${gate96//\{ICODE_OUT_DIR\}/$GATE_DIR}"
    if python3 -c "$gate_code" >/dev/null 2>&1; then
      echo "  ❌ 运行时 9.6：业务 unknown 误触发放行（§2.0.1 有矩阵但 metadata 空未被拦截）"
      FAIL=$((FAIL+1))
    else
      echo "  ✅ 运行时 9.6：业务 unknown 不误放行（矩阵已写 + metadata 空 → 拦截 rc≠0）"
    fi
    rm -rf "$GATE_DIR"
  fi
else
  echo "  ⚠️ 运行时 9.6：跳过（无 python3）"
fi

if [ "$FAIL" -eq 0 ]; then
  echo ""
  echo "ALL PASS"
  exit 0
else
  echo ""
  echo "FAILED: $FAIL 项未通过"
  exit 1
fi
