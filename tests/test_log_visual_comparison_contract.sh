#!/usr/bin/env bash
# tests/test_log_visual_comparison_contract.sh — 验证 log 工单「修复前后可读对照」契约
#
# 用法：bash tests/test_log_visual_comparison_contract.sh
# 退出码：0 = 全部通过；非 0 = 失败（带详细输出）
#
# 覆盖三阶段（对齐 steps/log.md「修复前后对照契约」的 RED→GREEN→REFACTOR）：
#   RED      静态契约锚点：steps/log.md / steps/00_init.md / references/anti_laziness.md
#            必须已含 §7.5 修复前后对照契约（实施前这些锚点不存在 → 测试失败 = RED 基线固化）
#   GREEN    语义 fixture：复杂场景（同一张 Mermaid 图含两个 subgraph + 图例 + 新旧关键点表
#            + 未部署声明 + 事实/待证/目标标识 + operation 边界 + 保留安全保护）；
#            简单场景（低复杂度用对照表、不强制 Mermaid）
#   REFACTOR 防回归：契约锚点 + 测试脚本自身存在性（spec §6「REFACTOR / 防回归」）
#
# 不依赖真实工程——只用 /tmp 内嵌 fixture，可重复运行。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DOC="$REPO_ROOT/steps/log.md"
INIT_DOC="$REPO_ROOT/steps/00_init.md"
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
    echo "  ❌ $desc — 缺失 $file"
    FAIL=$((FAIL+1))
  fi
}

echo "================================================"
echo "阶段 1 (RED/REFACTOR)：静态契约锚点"
echo "================================================"
echo "▶ steps/log.md — §7.5 契约必须已落地"
assert_contains "$LOG_DOC" "### §7.5 用户可读的修复前后对照" "log.md 报告骨架含 §7.5 小节"
assert_contains "$LOG_DOC" "修复前后对照契约" "log.md 含修复前后对照契约（触发判定 + 产物要求）"
assert_contains "$LOG_DOC" "低复杂度，不画图" "log.md 含低复杂度不强制 Mermaid 判定"
assert_contains "$LOG_DOC" "尚未部署" "log.md 含未部署声明（目标链路为设计建议）"
assert_contains "$LOG_DOC" "触发式必填" "log.md 必填/可空说明含 §7.5 触发式必填"

echo "▶ steps/00_init.md — 与 §7.5 的一致性约束必须已落地"
assert_contains "$INIT_DOC" "由该图压缩而来" "00_init.md 链路图含「由 §7.5 图压缩而来」一致性约束"

echo "▶ references/anti_laziness.md — 反偷懒第 34 条必须已落地"
assert_contains "$ANTI_DOC" "34. **复杂日志修复未提供可读新旧对照" "anti_laziness 含第 34 条（可读新旧对照）"

echo "▶ 测试脚本自身存在性（REFACTOR 防回归）"
assert_file "$REPO_ROOT/tests/test_log_visual_comparison_contract.sh" "测试脚本存在"

echo "▶ steps/07_readme.md — 对外简报修复前后链路继承 §7.5（readme 简报契约）"
assert_contains "$REPO_ROOT/steps/07_readme.md" "继承 log_analysis §7.5" "07_readme.md 简报修复前后链路继承 §7.5"
assert_contains "$REPO_ROOT/steps/07_readme.md" "低复杂度用 2–4 行对照表并注明" "07_readme.md 简报含低复杂度降级判定"

echo
echo "================================================"
echo "阶段 2 (GREEN)：语义 fixture — 复杂场景"
echo "================================================"

TMP_DIR=$(mktemp -d -t icode_log_vis_XXXXX)
trap 'rm -rf "$TMP_DIR"' EXIT

# 复杂 fixture：异步 timeout + 安全中断 + 下游错误码，同一张 Mermaid 图两个 subgraph
cat > "$TMP_DIR/complex.md" <<'EOF'
# 日志根因分析报告：异步 timeout + 安全中断 + 下游错误码

## 7. 修复设计 + 4 维度验证清单
### §7.1 A 档·根因修复
### §7.2 B 档·兜底/防御
### §7.5 用户可读的修复前后对照
复杂场景（异步 timeout + 安全中断 + 下游错误码），同图对照：

```mermaid
flowchart LR
  subgraph Before["修复前：现场链路"]
    direction TB
    B1["[事实] 触发事件"] --> B2["[事实] 在途状态"]
    B2 -. "[待证] 未完成收口/替代解释" .-> B3["[事实] 故障终态"]
    B3 --> B4["[事实] 下游失败或错误码"]
  end

  subgraph After["修复后：目标链路（未部署）"]
    direction TB
    A1["[目标] 冻结风险动作并保存 identity"] --> A2["[目标] 有界取消/释放"]
    A2 --> A3{"[目标] 已确认可复用？"}
    A3 -->|"否"| A4["[目标] fail-closed / 受控重试"]
    A3 -->|"是"| A5["[目标] 新 identity 的新任务"]
    A5 --> A6["[目标] 结果质量/边界准入"]
    A6 --> A7["[目标] 导航/业务准入"]
  end
```

| 维度 | 修复前 | 修复后 | 证据/验证 |
| --- | --- | --- | --- |
| 中断收口 | 在途任务未收口即复用，晚到结果污染 | 冻结风险动作并保存 identity，有界取消/释放 | 日志行 nodeX 12:00:00 触发现有失败 |
| 复用判定 | 无条件复用 | 已确认可复用才创建新 identity 任务 | 修复设计 §7.1 P 点 |

图例：`实线 = 已观察事实或目标控制流`、`虚线 = 待证因果/替代解释`、`[目标] = 尚未部署的设计`。
保留的安全保护：结果准入 gate 为新增，但既有边界保护（fail-closed 分支）仍保留。
EOF

python3 - "$TMP_DIR/complex.md" <<'PY' || { echo "  ❌ 复杂 fixture 契约校验失败"; FAIL=$((FAIL+1)); }
import sys
text = open(sys.argv[1], encoding='utf-8').read()
sec = text.split('### §7.5', 1)[1]
errs = []
if sec.count('subgraph') < 2:
    errs.append('同一张图至少 2 个 subgraph')
if '修复前：现场链路' not in sec or '修复后：目标链路' not in sec:
    errs.append('Before/After 两个 subgraph 标题分离（operation 边界）')
if '未部署' not in sec:
    errs.append('after 子图含「未部署」声明')
if '实线' not in sec or '虚线' not in sec:
    errs.append('含图例（实线/虚线）')
if '| 维度 | 修复前 | 修复后 | 证据/验证 |' not in sec:
    errs.append('含新旧关键点表')
if '[事实]' not in sec:
    errs.append('含 [事实] 标识')
if '[待证]' not in sec:
    errs.append('含 [待证] 标识')
if '[目标]' not in sec:
    errs.append('含 [目标] 标识')
if '保留的安全保护' not in sec:
    errs.append('含「保留的安全保护」说明')
if errs:
    for e in errs:
        print('    ❌ 复杂 fixture 缺: ' + e)
    sys.exit(1)
print('  ✅ 复杂 fixture：同图两 subgraph + 图例 + 新旧关键点表 + 未部署声明 + 事实/待证/目标标识 + operation 边界 + 保留安全保护')
PY

echo
echo "================================================"
echo "阶段 2 (GREEN)：语义 fixture — 简单场景"
echo "================================================"

# 简单 fixture：单函数空指针修复，低复杂度用 2-4 行对照表、不强制 Mermaid
cat > "$TMP_DIR/simple.md" <<'EOF'
# 日志根因分析报告：单函数空指针

## 7. 修复设计 + 4 维度验证清单
### §7.1 A 档·根因修复
### §7.5 用户可读的修复前后对照
低复杂度（单函数空指针修复，无分支），不画图：

| 修复前 | 修复后 | 验证信号 |
| --- | --- | --- |
| 解引用未判空 → 崩溃 | 判空后 fail-closed 返回错误码 | 不再崩溃，返回错误码 |
EOF

python3 - "$TMP_DIR/simple.md" <<'PY' || { echo "  ❌ 简单 fixture 契约校验失败"; FAIL=$((FAIL+1)); }
import sys
text = open(sys.argv[1], encoding='utf-8').read()
sec = text.split('### §7.5', 1)[1]
errs = []
if '低复杂度' not in sec:
    errs.append('声明「低复杂度」')
if '| 修复前 | 修复后 | 验证信号 |' not in sec:
    errs.append('含 2-4 行「修复前/修复后/验证信号」对照表')
if 'subgraph' in sec:
    errs.append('低复杂度不应强制 Mermaid 长流程图（无 subgraph）')
if errs:
    for e in errs:
        print('    ❌ 简单 fixture 缺/错: ' + e)
    sys.exit(1)
print('  ✅ 简单 fixture：低复杂度用对照表、不强制 Mermaid')
PY

echo
echo "================================================"
if [ "$FAIL" -eq 0 ]; then
  echo "🎉 全部通过 — log 工单修复前后可读对照契约（RED 静态锚点 + GREEN 复杂/简单 fixture + REFACTOR 防回归）"
  exit 0
else
  echo "❌ $FAIL 项失败 — 见上方 ❌ 行"
  exit 1
fi
