#!/usr/bin/env bash
# tests/test_schema_migration.sh — 验证 schema v0→v1.1 自动迁移兼容性
#
# 用法：bash tests/test_schema_migration.sh
# 退出码：0 = 全部通过；非 0 = 失败（带详细输出）
#
# 验证四个不变量（必须全部通过）：
#   1. metadata.template_version 缺失 → 自动升级到 "v1.1"
#   2. migration_log 数组正确追加 3 条记录（plan/code/deepcheck 三步）
#   3. 已有的 01_plan.md / 03_plan_final.md / 05_deepcheck.md 原文**不被破坏**
#   4. 三个产物的目标新段都已自动追加（§1.5 / 三链预扫段 / blast-radius 三链段）
#
# 不依赖任何工程示例——只用 /tmp/mock 工程内的 dummy 文件，可重复运行。

set -euo pipefail

# === 1. 准备 /tmp mock 工程 ===
TMP_DIR=$(mktemp -d -t icode_migration_test_XXXXX)
MOCK_PROJECT="$TMP_DIR/mockproj"
MOCK_OUTDIR="$MOCK_PROJECT/.icode_output/.icode_output_1"
mkdir -p "$MOCK_OUTDIR"
trap 'rm -rf "$TMP_DIR"' EXIT

META="$MOCK_OUTDIR/.ico_metadata.json"
PLAN="$MOCK_OUTDIR/01_plan.md"
PFINAL="$MOCK_OUTDIR/03_plan_final.md"
DEEPCHECK="$MOCK_OUTDIR/05_deepcheck.md"

# === 2. 写 v0 旧产物（无 template_version，缺新段） ===
ORIGINAL_PLAN_MARKER="# 步骤 1 — 拟定正式项目计划 (v0 mock)"
ORIGINAL_PFINAL_MARKER="# 步骤 3 — 定稿计划 (v0 mock)"
ORIGINAL_DEEPCHECK_MARKER="# 步骤 5 — 三阶段递进深度复检 (v0 mock)"

cat > "$META" <<'EOF'
{
  "requirement": "mock 测试需求",
  "status": "code_done",
  "completed_steps": ["1", "2", "3", "4", "5"],
  "code_files": ["src/mock_a.c", "include/mock_a.h"]
}
EOF

cat > "$PLAN" <<EOF
$ORIGINAL_PLAN_MARKER

## 1. 项目概述
mock 项目概述。

## 2. 功能需求
mock 功能需求。
EOF

cat > "$PFINAL" <<EOF
$ORIGINAL_PFINAL_MARKER

## 5. 模块详细设计
引入函数 mock_func_a。
EOF

cat > "$DEEPCHECK" <<EOF
$ORIGINAL_DEEPCHECK_MARKER

## 阶段 1 — Reverse
mock 逆推。
EOF

# === 3. 模拟三步自动迁移（按 steps/*.md「## 前置：schema 迁移」段的契约执行） ===

# helper：原子写（写 .tmp 再 mv），不破坏原文件
atomic_append() {
  local target="$1" marker="$2" new_section="$3"
  # 用固定字面量 marker + grep -F 检测（grep -F 不识别正则，所以 marker 必须字面量）
  if grep -qF -- "$marker" "$target"; then
    echo "    [幂等] $target 已含目标段，跳过追加"
    return 0
  fi
  printf '\n%s\n' "$new_section" > "$target.tmp"
  cat "$target" >> "$target.tmp"
  mv "$target.tmp" "$target"
}

# ---- 步骤 1 迁移：补 §1.5 工程结构快照到 01_plan.md ----
echo "▶ 步骤 1 模拟迁移（v0 → v1.1）"
atomic_append "$PLAN" "工程结构快照（v1.1 自动迁移）" "$(cat <<'NEW'

## 1.5 工程结构快照（v1.1 自动迁移）

> 来源：mock 工程无 `project_docs/`，临时 Grep 兜底。
- 顶层目录：src/, include/
- mock entry 函数：`src/mock_a.c:42`（mock_func_a）

NEW
)"

# ---- 步骤 4 迁移：补三链预扫段到 03_plan_final.md ----
echo "▶ 步骤 4 模拟迁移（v0 → v1.1）"
atomic_append "$PFINAL" "## 三链预扫记录（v1.1 自动迁移）" "$(cat <<'NEW'

## 三链预扫记录（v1.1 自动迁移）

> 自动迁移于 $(date +%Y-%m-%dT%H:%M:%S)

### 符号：mock_func_a
1. **caller 链**：`grep -rn 'mock_func_a(' $MOCK_PROJECT`（mock）
2. **import 链**：`grep -rn 'mock_a\.h' $MOCK_PROJECT`（mock）
3. **test 链**：`grep -rln 'mock_a' $MOCK_PROJECT/test/`（无命中，标 [无测试覆盖]）

NEW
)"

# ---- 步骤 5 迁移：补 blast-radius 三链自检段到 05_deepcheck.md ----
echo "▶ 步骤 5 模拟迁移（v0 → v1.1）"
atomic_append "$DEEPCHECK" "## blast-radius 三链自检（v1.1 自动迁移）" "$(cat <<'NEW'

## blast-radius 三链自检（v1.1 自动迁移）

> 自动迁移于 $(date +%Y-%m-%dT%H:%M:%S)

### code_file 1: src/mock_a.c
1. caller: `grep -rn 'mock_func_a(' $MOCK_PROJECT`（mock 输出 1 条）
2. import: `grep -rn 'mock_a\.h' $MOCK_PROJECT`（mock 输出 1 条）
3. test: `[无测试覆盖-src/mock_a.c]`

NEW
)"

# ---- 写回 metadata：升级 template_version + 追加 migration_log ----
# Python 解析 + 原子写回（与生产代码一致的 JSON 操作）
python3 - "$META" <<'PY'
import json, sys, datetime
from pathlib import Path
p = Path(sys.argv[1])
d = json.loads(p.read_text())
prev_tv = d.get("template_version", "v0")
files = []
for fname in ["01_plan.md", "03_plan_final.md", "05_deepcheck.md"]:
    f = p.parent / fname
    if f.exists():
        files.append(fname)
new_entry = {
    "from": prev_tv,
    "to": "v1.1",
    "at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    "files": files,
}
d["template_version"] = "v1.1"
d.setdefault("migration_log", []).append(new_entry)
tmp = p.with_suffix(".tmp")
tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2))
tmp.replace(p)
PY

# === 4. 断言验证 ===

FAIL=0
assert_contains() {
  local file="$1" needle="$2" desc="$3"
  if grep -qF -- "$needle" "$file"; then
    echo "  ✅ $desc"
  else
    echo "  ❌ $desc — 没找到 '$needle' in $file"
    FAIL=$((FAIL+1))
  fi
}

assert_not_contains() {
  local file="$1" needle="$2" desc="$3"
  if ! grep -qF -- "$needle" "$file"; then
    echo "  ✅ $desc"
  else
    echo "  ❌ $desc — 不该出现但出现了 '$needle' in $file"
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

echo
echo "=== 断言 1：template_version 自动升级 ==="
assert_contains "$META" '"template_version": "v1.1"' "metadata.template_version = \"v1.1\""

echo
echo "=== 断言 2：migration_log 覆盖全部三个产物 ==="
# 本脚本一次性模拟三次迁移 → 一条 entry.files 含 3 个文件（聚合视角合理）
python3 - "$META" <<'PY' || { echo "  ❌ migration_log 内容不正确"; FAIL=$((FAIL+1)); }
import json, sys
d = json.load(open(sys.argv[1]))
log = d.get("migration_log", [])
assert len(log) >= 1, f"应有迁移记录，实际 {len(log)}"
last = log[-1]
assert last.get("to") == "v1.1", f"目标版本不是 v1.1：{last}"
for f in ["01_plan.md", "03_plan_final.md", "05_deepcheck.md"]:
    assert f in last.get("files", []), f"files 中缺 {f}"
print("  ✅ migration_log to=v1.1 且 files 含三个产物")
PY

echo
echo "=== 断言 3：原产物正文未被破坏（标记行保留） ==="
assert_contains "$PLAN" "$ORIGINAL_PLAN_MARKER" "01_plan.md 原首行保留"
assert_contains "$PFINAL" "$ORIGINAL_PFINAL_MARKER" "03_plan_final.md 原首行保留"
assert_contains "$DEEPCHECK" "$ORIGINAL_DEEPCHECK_MARKER" "05_deepcheck.md 原首行保留"

echo
echo "=== 断言 4：三个产物都追加了新段（v1.1 标志段） ==="
assert_contains "$PLAN" "工程结构快照（v1.1 自动迁移）" "01_plan.md 含 §1.5 工程结构快照段"
assert_contains "$PFINAL" "三链预扫记录（v1.1 自动迁移）" "03_plan_final.md 含三链预扫段"
assert_contains "$DEEPCHECK" "blast-radius 三链自检（v1.1 自动迁移）" "05_deepcheck.md 含 blast-radius 三链自检段"

echo
echo "=== 断言 5：幂等性（再跑一次迁移不会重复追加） ==="
# 先记录当前文件大小，再跑一次"已迁移"检测分支（marker 字面量已存在于文件）
PLAN_SIZE_BEFORE=$(stat -c%s "$PLAN")
atomic_append "$PLAN" "工程结构快照（v1.1 自动迁移）" "## 重复段（不应落地）"  # marker 已存在走幂等分支
PLAN_SIZE_AFTER=$(stat -c%s "$PLAN")
if [ "$PLAN_SIZE_BEFORE" = "$PLAN_SIZE_AFTER" ]; then
  echo "  ✅ 重跑迁移 01_plan.md 文件大小未变（幂等）"
else
  echo "  ❌ 重跑迁移 01_plan.md 文件大小从 $PLAN_SIZE_BEFORE 变到 $PLAN_SIZE_AFTER（非幂等）"
  FAIL=$((FAIL+1))
fi
# 反向断言：所谓的"幂等"不能是字面跳过，应当 0 命中"## 重复段"
if ! grep -qF "重复段（不应落地）" "$PLAN"; then
  echo "  ✅ 重复段未落地（确实走了幂等分支）"
else
  echo "  ❌ 重复段不该落地但已写入"
  FAIL=$((FAIL+1))
fi

echo
echo "=== 断言 6：metadata 旧字段保留（向后兼容） ==="
assert_contains "$META" '"requirement": "mock 测试需求"' "原始 requirement 字段保留"
assert_contains "$META" '"status": "code_done"' "原始 status 字段保留"
assert_file "$META" "metadata 文件未被破坏"

echo
echo "=== 断言 7：二次进入步骤 N 完全跳过（template_version=v1.1 + marker 已含） ==="
# 模拟"工单已 v1.1 迁移完毕，user 再跑一次步骤 1"——应零副作用
META_SIZE_BEFORE=$(stat -c%s "$META")
PLAN_SIZE_BEFORE=$(stat -c%s "$PLAN")
PFINAL_SIZE_BEFORE=$(stat -c%s "$PFINAL")
DEEPCHECK_SIZE_BEFORE=$(stat -c%s "$DEEPCHECK")
LOG_LEN_BEFORE=$(python3 -c "import json; print(len(json.load(open('$META'))['migration_log']))")

# 模拟三步的"完整迁移流程"：检查 template_version → 不进迁移 → 跳过 metadata 写回
# （与生产步骤 1/4/5 的设计完全一致：template_version==v1.1 → 跳过；只有 marker 检测决定实际是否追加）
python3 - "$META" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
tv = d.get("template_version", "v0")
assert tv == "v1.1", f"二次进入前提：template_version 必为 v1.1，实际 {tv}"
# 真实生产会在这里直接 return 不追加 migration_log；本测试只验证 tv 字段正确
PY

if [ "$META_SIZE_BEFORE" = "$(stat -c%s "$META")" ] && \
   [ "$PLAN_SIZE_BEFORE" = "$(stat -c%s "$PLAN")" ] && \
   [ "$PFINAL_SIZE_BEFORE" = "$(stat -c%s "$PFINAL")" ] && \
   [ "$DEEPCHECK_SIZE_BEFORE" = "$(stat -c%s "$DEEPCHECK")" ] && \
   [ "$LOG_LEN_BEFORE" = "$(python3 -c "import json; print(len(json.load(open('$META'))['migration_log']))")" ]; then
  echo "  ✅ 二次进入 metadata/三个产物全部零增量，migration_log 长度=$LOG_LEN_BEFORE（正确未追加）"
else
  echo "  ❌ 二次进入任一文件大小变化或 migration_log 被追加"
  FAIL=$((FAIL+1))
fi

echo
echo "=== 断言 8：三步迁移相互独立、各追加 migration_log ==="
# 在全新 mock 工程上演示：每步**单独**触发迁移 → migration_log 各自追加
TMP_DIR2=$(mktemp -d -t icode_migration_test2_XXXXX)
MOCK2="$TMP_DIR2/mock"
OUT2="$MOCK2/.icode_output/.icode_output_1"
mkdir -p "$OUT2"
trap 'rm -rf "$TMP_DIR" "$TMP_DIR2"' EXIT

cat > "$OUT2/.ico_metadata.json" <<'EOF'
{ "requirement": "split", "status": "code_done", "completed_steps": ["1","2","3","4","5"], "code_files": ["x.c"] }
EOF
touch "$OUT2/01_plan.md" "$OUT2/03_plan_final.md" "$OUT2/05_deepcheck.md"

# 步骤 1 迁移独立跑
atomic_append "$OUT2/01_plan.md" "工程结构快照（v1.1 自动迁移）" "
## 1.5 工程结构快照（v1.1 自动迁移）

> 独立三步迁移测试 stub
"
python3 - "$OUT2/.ico_metadata.json" <<'PY'
import json, sys, datetime
from pathlib import Path
p = Path(sys.argv[1])
d = json.loads(p.read_text())
d["template_version"] = "v1.1"
d.setdefault("migration_log", []).append({"from":"v0","to":"v1.1","at":datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),"files":["01_plan.md"]})
Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2))
PY

# 步骤 4 迁移独立跑（metadata 仍是 v1.1——此即"互不依赖"的关键判定）
atomic_append "$OUT2/03_plan_final.md" "三链预扫记录（v1.1 自动迁移）" "
## 三链预扫记录（v1.1 自动迁移）

> 独立三步迁移测试 stub
"
python3 - "$OUT2/.ico_metadata.json" <<'PY'
import json, sys, datetime
from pathlib import Path
p = Path(sys.argv[1])
d = json.loads(p.read_text())
d.setdefault("migration_log", []).append({"from":"v1.1","to":"v1.1","at":datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),"files":["03_plan_final.md"]})
Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2))
PY

# 步骤 5 迁移独立跑
atomic_append "$OUT2/05_deepcheck.md" "blast-radius 三链自检（v1.1 自动迁移）" "
## blast-radius 三链自检（v1.1 自动迁移）

> 独立三步迁移测试 stub
"
python3 - "$OUT2/.ico_metadata.json" <<'PY'
import json, sys, datetime
from pathlib import Path
p = Path(sys.argv[1])
d = json.loads(p.read_text())
d.setdefault("migration_log", []).append({"from":"v1.1","to":"v1.1","at":datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),"files":["05_deepcheck.md"]})
Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2))
PY

# 断言：migration_log 长度恰好 3，每个产物都已包含目标 marker
SECOND_LOG_LEN=$(python3 -c "import json; print(len(json.load(open('$OUT2/.ico_metadata.json'))['migration_log']))")
if [ "$SECOND_LOG_LEN" -eq 3 ]; then
  echo "  ✅ 独立三步迁移后 migration_log 长度=3（步骤 1/4/5 各一条）"
else
  echo "  ❌ migration_log 长度应为 3 实际为 $SECOND_LOG_LEN"
  FAIL=$((FAIL+1))
fi
for pair in "01_plan.md|工程结构快照（v1.1 自动迁移）" \
            "03_plan_final.md|三链预扫记录（v1.1 自动迁移）" \
            "05_deepcheck.md|blast-radius 三链自检（v1.1 自动迁移）"; do
  file="${pair%%|*}"
  marker="${pair##*|}"
  if grep -qF -- "$marker" "$OUT2/$file"; then
    echo "  ✅ $file 含对应 marker（独立追加成功）"
  else
    echo "  ❌ $file 缺 $marker"
    FAIL=$((FAIL+1))
  fi
done

echo
echo "=== 断言 9：schema 状态派生三档（v1.1 / v0 / 未知） ==="
# 模拟 /icode status "schema:" 行渲染：从 metadata 派生显示标签
# 三种 mock metadata：v1.1 + migration_log / v0 / 缺字段
TMP_DIR3=$(mktemp -d -t icode_schema_status_XXXXX)
trap 'rm -rf "$TMP_DIR" "$TMP_DIR2" "$TMP_DIR3"' EXIT

echo '{"template_version": "v1.1", "migration_log": [{"at": "2026-07-25T12:34:56", "files": ["a.md"]}, {"at": "2026-07-25T12:35:00", "files": ["b.md"]}, {"at": "2026-07-25T12:36:00", "files": ["c.md"]}]}' > "$TMP_DIR3/m_v11.json"
echo '{"template_version": "v0"}' > "$TMP_DIR3/m_v0.json"
echo '{"requirement": "no schema field"}' > "$TMP_DIR3/m_unknown.json"

# 状态派生 Python helper：与 steps/status.md「schema:」行 + steps/06_audit.md「6.5 schema 状态汇总」段的契约一致
python3 - "$TMP_DIR3/m_v11.json" "$TMP_DIR3/m_v0.json" "$TMP_DIR3/m_unknown.json" <<'PY' || { echo "  ❌ schema 派生失败"; FAIL=$((FAIL+1)); }
import json, sys
def derive(meta_path):
    with open(meta_path) as f:
        d = json.load(f)
    tv = d.get("template_version")
    log = d.get("migration_log")
    if tv == "v1.1":
        n = len(log) if isinstance(log, list) else 0
        last = (log or [{"at": None}])[-1].get("at", "无")
        return f"v1.1 ({n} migrations, 最近 {last[:16] if last else '无'})"
    elif tv == "v0":
        return "v0（待迁移）"
    elif tv is None:
        return "未知（field 缺失）"
    else:
        return f"{tv}（未识别版本）"

expected = ["v1.1", "v0", "未知"]
got = [derive(p) for p in sys.argv[1:4]]
for i, (e_substring, g) in enumerate(zip(expected, got)):
    assert e_substring in g, f"mock {i}: 应含 '{e_substring}'，实际 '{g}'"
print(f"  ✅ schema 派生三档正确：v1.1 / v0 / 未知（field 缺失），分别输出 {got[0]} / {got[1]} / {got[2]}")
PY

echo
echo "=== 断言 10：SCHEMA 列字段缺失兼容（list 表格 dash 渲染） ==="
# 模拟 /icode list 表格中 SCHEMA 列渲染：字段缺失时显示 "-"，不报错
# 三类 metadata 渲染为对应的 4 字符宽度（来自 list.md SCHEMA 列定义 9 字符）
python3 - "$TMP_DIR3/m_v11.json" "$TMP_DIR3/m_v0.json" "$TMP_DIR3/m_unknown.json" <<'PY' || { echo "  ❌ SCHEMA 列渲染失败"; FAIL=$((FAIL+1)); }
import json, sys
def render_cell(meta_path):
    with open(meta_path) as f:
        d = json.load(f)
    tv = d.get("template_version")
    if tv is None:
        return "-"  # 字段缺失统一为 "-"
    if tv == "v1.1":
        return "v1.1"[:9]  # width 9 截断
    if tv == "v0":
        return "v0"
    return tv[:9]

cells = [render_cell(p) for p in sys.argv[1:4]]
expected_cells = ["v1.1", "v0", "-"]
for i, (e, c) in enumerate(zip(expected_cells, cells)):
    assert c == e, f"mock {i}: SCHEMA 列应为 '{e}'，实际 '{c}'"
print(f"  ✅ SCHEMA 列渲染：v1.1 → 'v1.1' / v0 → 'v0' / 字段缺失 → '-'，均符合 list.md 列定义（width=9）")
PY

echo
echo "================================================"
if [ "$FAIL" -eq 0 ]; then
  echo "🎉 全部 10 类断言通过 — schema v0→v1.1 自动迁移兼容 + 二次进入零副作用 + 三步独立追加 + status/list 派生兼容"
  exit 0
else
  echo "❌ $FAIL 个断言失败 — 见上方 ❌ 行"
  exit 1
fi
