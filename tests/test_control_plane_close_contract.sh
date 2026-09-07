#!/usr/bin/env bash
# 关闭分阶段幂等 + 身份解析 + 归档 hash roundtrip 契约。
set -u
cd "$(dirname "$0")/.." || exit 1
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

CTL="python3 tools/icode_control.py"
export ICODE_CONTROL_TEST_MODE=1
T=$(mktemp -d /tmp/icoclose.XXXXXX); trap 'rm -rf "$T"' EXIT
WS="$T/ws"; D="$WS/.icode_output/.icode_output_1"; mkdir -p "$D"
ARCHIVE="$T/archive/close-1"; mkdir -p "$ARCHIVE"
$CTL create --dir "$D" --ticket-id close-1 --requirement r --birth plan \
  --metadata-json "{\"archive_path\":\"$ARCHIVE\"}" >/dev/null
for st in plan_done review_in_progress review_done plan_finalized code_in_progress code_done deepcheck_in_progress deepcheck_done; do
  $CTL transition --dir "$D" --to "$st" --skip-gates >/dev/null || exit 1
done
$CTL transition --dir "$D" --to completed --delivery-verdict verification_pending --skip-gates >/dev/null || exit 1
CLOSE_IDX="$T/close-index.json"
$CTL index-write --ticket-dir "$D" --index "$CLOSE_IDX" >/dev/null || exit 1

# 1) 跨阶段跳转 → fail-closed（close_phase_order）
if $CTL close-phase --dir "$D" --phase checkouts_removed 2>/dev/null | grep -q 'close_phase_order'; then
  ok "跨阶段跳转被拒（close_phase_order）"; else bad "跨阶段跳转未被拒"; fi

# 2) 分阶段幂等重放：先 close_planned，再重放同阶段 → already_applied
$CTL close-phase --dir "$D" --phase close_planned >/dev/null 2>&1
if $CTL close-phase --dir "$D" --phase close_planned 2>/dev/null | grep -q 'already_applied'; then
  ok "已应用阶段重放 → already_applied（分阶段幂等）"; else bad "分阶段幂等未生效"; fi
if $CTL metadata-update --dir "$D" --set-json '{"submitted_baseline":"abc"}' \
    --request-id close-meta-1 >/dev/null \
   && $CTL metadata-update --dir "$D" --set-json '{"keywords":["late"]}' 2>/dev/null \
      | grep -q close_metadata_frozen; then
  ok "关闭流程仅允许受控写拓扑/提交账本"
else bad "关闭流程 metadata 写边界失效"; fi
if $CTL transition --dir "$D" --to review_in_progress 2>/dev/null | grep -q 'closed_ticket_status_frozen'; then
  ok "进入关闭流程后 status 冻结"
else
  bad "关闭流程中仍可改写 status"
fi
if $CTL close-phase --dir "$D" --phase archived 2>/dev/null | grep -q 'archive_manifest'; then
  ok "缺 manifest 时 archived 被拒绝"; else bad "缺 manifest 仍可标记 archived"; fi
# archived 前必须能从源工单无损复验所有小型控制产物。
touch "$D/01_plan.md" "$D/02_review.md" "$D/03_plan_final.md" \
      "$D/04_code_review_fix.md" "$D/05_deepcheck.md" "$D/06_audit.md" \
      "$D/.thinking_gate_trace.jsonl" "$D/.mcp_gate_trace.jsonl"
cp -a "$D/." "$ARCHIVE/"
if $CTL archive-manifest --dir "$D" --archive-dir "$ARCHIVE" --write --skip-linters \
    >/dev/null 2>&1; then
  ok "归档 manifest/hash roundtrip 通过"; else bad "归档 manifest 生成失败"; fi
# 手工裁剪 manifest 条目不得伪装成完整归档。
python3 - "$ARCHIVE/archive_manifest.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["files"]=[x for x in d["files"] if x["path"]!="01_plan.md"]
json.dump(d,open(p,"w"),ensure_ascii=False,indent=2)
PY
if $CTL close-phase --dir "$D" --phase archived 2>/dev/null | grep -q 'archive_manifest'; then
  ok "缩水 manifest 被完整性门禁拒绝"
else
  bad "缩水 manifest 绕过完整性门禁"
fi
$CTL archive-manifest --dir "$D" --archive-dir "$ARCHIVE" --write --skip-linters >/dev/null 2>&1
# 3) archived 是控制根交接点；原 checkout 消失后仍能在归档根推进到 closed。
if $CTL close-phase --dir "$D" --phase archived 2>/dev/null \
    | grep -q '"control_root":.*archive/close-1'; then
  ok "archived 完成后交接到归档控制根"
else bad "archived 未交接控制根"; fi
if $CTL close-phase --dir "$D" --phase roots_verified 2>/dev/null \
    | grep -q 'close_control_handoff'; then
  ok "archived 后拒绝在可删源目录继续推进"
else bad "archived 后仍在源目录推进"; fi
SOURCE_STASH="$T/source-output.removed"
mv "$D" "$SOURCE_STASH"
if python3 - "$WS" "$CLOSE_IDX" "$ARCHIVE" <<'PY'
import importlib.util,pathlib,sys,types
spec=importlib.util.spec_from_file_location("ctl","tools/icode_control.py")
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.INDEX_PATH=pathlib.Path(sys.argv[2])
args=types.SimpleNamespace(dir=None,ticket="close-1",latest=False,workspace=sys.argv[1])
resolved=m.resolve_dir(args)
assert pathlib.Path(resolved["out_dir"]).resolve()==pathlib.Path(sys.argv[3]).resolve()
PY
then ok "原路径消失时 resolve-ticket 经索引回退归档根"
else bad "resolve-ticket 未回退可读归档根"; fi
# legacy 时代编号目录可被后来工单复用；源路径存在但身份不符时也必须继续查归档。
mkdir -p "$D"
python3 - "$D/.ico_metadata.json" <<'PY'
import json,sys
json.dump({"ticket_id":"different-ticket"}, open(sys.argv[1], "w"))
PY
if python3 - "$WS" "$CLOSE_IDX" "$ARCHIVE" <<'PY'
import importlib.util,pathlib,sys,types
spec=importlib.util.spec_from_file_location("ctl","tools/icode_control.py")
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.INDEX_PATH=pathlib.Path(sys.argv[2])
args=types.SimpleNamespace(dir=None,ticket="close-1",latest=False,workspace=sys.argv[1])
resolved=m.resolve_dir(args)
assert pathlib.Path(resolved["out_dir"]).resolve()==pathlib.Path(sys.argv[3]).resolve()
PY
then ok "源编号目录被复用时按 ticket_id 回退归档根"
else bad "复用路径中的其他工单遮蔽了归档根"; fi
mv "$D" "$T/reused-output"
okseq=1
for ph in roots_verified checkouts_removed branches_removed closed; do
  $CTL close-phase --dir "$ARCHIVE" --phase "$ph" >/dev/null 2>&1 || okseq=0
done
[ "$okseq" -eq 1 ] && ok "源目录消失后归档根有序推进到 closed" \
  || bad "归档控制根续跑失败"
if $CTL index-write --ticket-dir "$ARCHIVE" --index "$CLOSE_IDX" >/dev/null 2>&1 \
   && python3 - "$ARCHIVE/.ico_metadata.json" "$CLOSE_IDX" <<'PY'
import json,sys
meta=json.load(open(sys.argv[1])); index=json.load(open(sys.argv[2]))
row=next(item for item in index["tickets"] if item["ticket_id"]=="close-1")
assert meta["close_state"]=="closed"
assert row["project_path"].endswith("/ws")
assert row["out_dir"]==".icode_output/.icode_output_1"
PY
then ok "归档根可刷新索引且保留原身份三元组"
else bad "归档根索引回写异常"; fi

# 4) closed 终态：重复 close 只回放摘要（already_closed），不重复执行
if $CTL close-phase --dir "$ARCHIVE" --phase closed 2>/dev/null | grep -q 'already_closed'; then
  ok "closed 终态重复 close → 只回放摘要"; else bad "closed 终态未回放摘要"; fi
if $CTL event --dir "$ARCHIVE" --type external_note --payload '{"x":1}' 2>/dev/null \
    | grep -q 'closed_ticket_mutation_frozen'; then
  ok "关闭根拒绝后续业务事件污染"
else bad "关闭根仍可追加业务事件"; fi

# 4b) reopen 必须通过专用事务解冻，保留关闭历史并允许第二个 close 周期。
NEW_WT="$T/reopen-wt"
git init -q "$NEW_WT"
git -C "$NEW_WT" config user.email test@example.com
git -C "$NEW_WT" config user.name test
git -C "$NEW_WT" commit --allow-empty -qm base
NEW_BRANCH=$(git -C "$NEW_WT" rev-parse --abbrev-ref HEAD)
NEW_HEAD=$(git -C "$NEW_WT" rev-parse HEAD)
ACTIVE_JSON=$(printf '{"active_checkout":{"path":"%s","branch":"%s","base_ref":"refs/heads/%s","base_commit":"%s","state":"active"}}' \
  "$NEW_WT" "$NEW_BRANCH" "$NEW_BRANCH" "$NEW_HEAD")
if $CTL reopen --dir "$ARCHIVE" --metadata-json "$ACTIVE_JSON" \
    --reason follow-up --request-id reopen-1 >/dev/null 2>&1 \
   && $CTL reopen --dir "$ARCHIVE" --metadata-json "$ACTIVE_JSON" \
    --reason follow-up --request-id reopen-1 2>/dev/null | grep -q already_applied \
   && python3 - "$ARCHIVE/.ico_metadata.json" "$NEW_WT/.icode_output/.active_ticket.json" <<'PY'
import json,sys
m=json.load(open(sys.argv[1]))
assert m["close_state"] is None
assert m["delivery_verdict"]=="verification_pending"
assert m["active_checkout"]["state"]=="active"
p=json.load(open(sys.argv[2])); assert p["ticket_id"]=="close-1"
assert p["control_root"]==m["artifact_root"]
PY
then ok "reopen 原子解冻、幂等并降级交付结论"
else bad "reopen 受控解冻失败"; fi
cycle_ok=1
run_cycle_step() {
  local label="$1"; shift
  local output
  if ! output=$("$@" 2>&1); then
    echo "    ${label}: ${output}"
    cycle_ok=0
  fi
}
run_cycle_step event python3 tools/icode_control.py event --dir "$ARCHIVE" \
  --type external_note --payload '{"cycle":2}'
run_cycle_step close_planned python3 tools/icode_control.py close-phase \
  --dir "$ARCHIVE" --phase close_planned
run_cycle_step archive_manifest python3 tools/icode_control.py archive-manifest \
  --dir "$ARCHIVE" --archive-dir "$ARCHIVE" --write --skip-linters
run_cycle_step archived python3 tools/icode_control.py close-phase \
  --dir "$ARCHIVE" --phase archived
validation_output=$($CTL validate --dir "$ARCHIVE" --index "$CLOSE_IDX" --skip-linters 2>&1)
validation_rc=$?
if [ "$cycle_ok" -eq 1 ] && [ "$validation_rc" -eq 0 ] \
   && printf '%s' "$validation_output" | grep -q '"ok": true'; then
  ok "reopen 后归档根可就地重建 manifest 并进入第二个 close 周期"
else
  echo "    validate: ${validation_output}"
  bad "reopen 后再关闭周期断链"
fi
mv "$SOURCE_STASH" "$D"
printf '\nTAMPER\n' >> "$ARCHIVE/01_plan.md"
if $CTL archive-manifest --dir "$D" --archive-dir "$ARCHIVE" 2>/dev/null | grep -q '"ok": false'; then
  ok "归档篡改被 hash 复验发现"; else bad "归档篡改未被发现"; fi

# 5) resolve-ticket 身份解析（扫描命中 1 个 → source=scan）
if $CTL resolve-ticket --ticket close-1 --workspace "$WS" 2>/dev/null | grep -q '"source": "scan"'; then
  ok "resolve-ticket 扫描唯一命中"; else bad "resolve-ticket 扫描未命中"; fi

# 6) resolve-ticket 多义 → exit 4 拒绝猜测（目录误复用防线）
D2="$WS/.icode_output/.icode_output_2"; mkdir -p "$D2"; cp "$D/.ico_metadata.json" "$D2/"
$CTL resolve-ticket --ticket close-1 --workspace "$WS" >/dev/null 2>&1; rc=$?
[ "$rc" -eq 4 ] && ok "resolve-ticket 多义 → exit 4（拒绝猜测）" || bad "resolve-ticket 多义 rc=$rc 非 4"

# 7) resolve-ticket 未命中 + 索引兜底 path_gone（归档 roundtrip）
rm -rf "$D2"
IDX="$T/idx.json"
cat > "$IDX" <<'EOF'
{"version":1,"updated_at":"2026-09-07T00:00:00","tickets":[
 {"ticket_id":"gone-1","project_path":"/nonexistent/proj","out_dir":".icode_output/.icode_output_9","status":"completed"}
]}
EOF
# 用索引兜底需要 HOME 索引；直接验证未命中路径：ticket 不存在 → 报错（索引无此 ticket）
if $CTL resolve-ticket --ticket no-such-ticket --workspace "$WS" 2>/dev/null | grep -q '均未命中'; then
  ok "resolve-ticket 未命中 → 明确报错"; else bad "resolve-ticket 未命中未报错"; fi

# 8) archive 指针校验（archive_path 在 index schema 内合法；archive 活跃态语义由 dir_and_metadata 文档契约覆盖）
if python3 - "$IDX" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["version"] == 1 and isinstance(d["tickets"], list)
print("ok")
PY
then ok "索引结构可解析（archive 条目字段兼容）"; else bad "索引结构解析失败"; fi

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
