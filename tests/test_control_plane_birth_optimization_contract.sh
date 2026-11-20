#!/usr/bin/env bash
# Windows: python3 可能是 WindowsApps 假 stub（无输出、exit 49）；加载共享 shim
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/python3-shim.sh" 2>/dev/null || true
# 出生优化契约：
#   - debug 出生跳过全仓候选快照（事件 <100KB，无 candidate_tracking）
#   - init/debug 出生 seed semantic_decisions/requirement_deltas 空数组
#   - create --requirement-file / metadata-update --set-json-file 的 UTF-8 往返
#   - 文件入参与内联参数互斥；debug 拒绝候选参数
set -u
cd "$(dirname "$0")/.." || exit 1
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

CTL="python3 tools/icode_control.py"
export ICODE_CONTROL_TEST_MODE=1
T=$(mktemp -d "${TMPDIR:-/tmp}/icobirth.XXXXXX"); trap 'rm -rf "$T"' EXIT
R="$T/repo"; mkdir -p "$R"
git -C "$R" init -b main >/dev/null 2>&1
git -C "$R" config user.email birth@example.com
git -C "$R" config user.name birth
printf 'a\n' > "$R/a.txt"; printf 'b\n' > "$R/b.txt"
git -C "$R" add . >/dev/null 2>&1
git -C "$R" commit -m init >/dev/null 2>&1

REQ="$T/requirement.txt"
python3 - "$REQ" <<'PY'
import sys, pathlib
text = "中文需求：验证 PD23FB7g 双目适配，包含“引号”、换行与 emoji ✅。\n第二行。"
pathlib.Path(sys.argv[1]).write_bytes(text.encode("utf-8"))
PY

# 1) debug 出生：无候选快照、seed 空门禁字段、事件体积受控
DD="$R/.icode_output/.debug/.icode_output_1"; mkdir -p "$DD"
DEBUG_OUT=$($CTL create --dir "$DD" --ticket-id "debug:birth-1" --requirement-file "$REQ" --birth debug-init 2>&1)
if grep -q 'candidate scan: skipped (debug birth)' <<< "$DEBUG_OUT"; then
  ok "debug 出生输出候选扫描跳过摘要"
else bad "debug 出生缺少扫描摘要"; fi
SIZE=$(wc -c < "$DD/.ico_events.jsonl" | tr -d ' ')
if [ "$SIZE" -lt 100000 ]; then ok "debug 出生事件 <100KB（实际 $SIZE）"; else bad "debug 出生事件过大（$SIZE）"; fi
if python3 - "$DD" "$REQ" <<'PY'
import json, pathlib, sys
physical = json.loads((pathlib.Path(sys.argv[1]) / ".ico_metadata.json").read_text(encoding="utf-8"))
m = physical.get("value", physical)
expected = pathlib.Path(sys.argv[2]).read_bytes().decode("utf-8")
assert m.get("requirement") == expected, "requirement mismatch"
assert m.get("semantic_decisions") == [], "semantic_decisions not seeded"
assert m.get("requirement_deltas") == [], "requirement_deltas not seeded"
assert "candidate_tracking" not in m, "debug candidate_tracking present"
assert m.get("excluded_side_effects") is None, "debug excluded_side_effects present"
PY
then ok "debug metadata：UTF-8 需求/空门禁数组/无候选合同"; else bad "debug metadata 契约不符"; fi
if $CTL validate --dir "$DD" --skip-linters | grep -q '"ok": true'; then
  ok "debug schema validate 通过"
else bad "debug schema validate 失败"; fi
if python3 tools/lint_workflow_contract.py --strict --json "$DD" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("blocked")==0 else 1)'; then
  ok "debug workflow strict 通过（semantic_decisions 已 seed）"
else bad "debug workflow strict 失败"; fi

# 2) init 出生：文件入参 UTF-8 往返；候选合同仍启用
D="$R/.icode_output/.icode_output_1"; mkdir -p "$D"
INIT_OUT=$($CTL create --dir "$D" --ticket-id "birth-2" --requirement-file "$REQ" --birth init 2>&1)
if grep -Eq 'candidate scan: [0-9]+ files, [0-9]+ index rows, [0-9]+ ms' <<< "$INIT_OUT"; then
  ok "init 出生输出候选扫描摘要"
else bad "init 出生缺少扫描摘要"; fi
if python3 - "$D" "$REQ" <<'PY'
import json, pathlib, sys
physical = json.loads((pathlib.Path(sys.argv[1]) / ".ico_metadata.json").read_text(encoding="utf-8"))
m = physical.get("value", physical)
expected = pathlib.Path(sys.argv[2]).read_bytes().decode("utf-8")
assert m.get("requirement") == expected, "requirement mismatch"
assert isinstance(m.get("candidate_tracking"), dict), "init candidate_tracking missing"
assert m.get("semantic_decisions") == [], "semantic_decisions not seeded"
PY
then ok "init 出生：UTF-8 需求 + 候选合同保留 + 空门禁数组"; else bad "init 出生契约不符"; fi

# 3) metadata-update 文件入参 UTF-8 往返
U="$T/update.json"
python3 - "$U" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_bytes(json.dumps(
    {"requirement_summary": "中文摘要：验证文件入参", "keywords": ["中文", "文件入参"]},
    ensure_ascii=False).encode("utf-8"))
PY
$CTL metadata-update --dir "$D" --set-json-file "$U" --request-id birth-update-1 >/dev/null
if python3 - "$D" <<'PY'
import json, pathlib, sys
physical = json.loads((pathlib.Path(sys.argv[1]) / ".ico_metadata.json").read_text(encoding="utf-8"))
m = physical.get("value", physical)
assert m.get("requirement_summary") == "中文摘要：验证文件入参", m.get("requirement_summary")
PY
then ok "metadata-update --set-json-file 中文精确落盘"; else bad "metadata-update 文件入参不符"; fi

# 4) 互斥与 debug 候选参数拒绝
D2="$R/.icode_output/.icode_output_2"
if $CTL create --dir "$D2" --ticket-id "birth-3" --requirement x --requirement-file "$REQ" --birth init 2>&1 | grep -q '二选一'; then
  ok "--requirement 与 --requirement-file 互斥"
else bad "requirement 文件/内联未互斥"; fi
if $CTL metadata-update --dir "$D" --set-json '{}' --set-json-file "$U" 2>&1 | grep -q '二选一'; then
  ok "--set-json 与 --set-json-file 互斥"
else bad "set-json 文件/内联未互斥"; fi
DD2="$R/.icode_output/.debug/.icode_output_2"; mkdir -p "$DD2"
if $CTL create --dir "$DD2" --ticket-id "debug:birth-3" --requirement-file "$REQ" --birth debug-init --exclude x 2>&1 | grep -q '不适用'; then
  ok "debug 出生拒绝 --exclude（无候选合同）"
else bad "debug 出生未拒绝候选参数"; fi

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
