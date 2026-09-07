#!/usr/bin/env bash
# 控制面状态机/迁移/索引单一 writer 契约（§10: test_transition_fail_closed_contract /
#                       test_legacy_migration_idempotent / test_index_single_writer_contract /
#                       test_ticket_snapshot_resume）
set -u
cd "$(dirname "$0")/.." || exit 1
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

CTL="python3 tools/icode_control.py"
export ICODE_CONTROL_TEST_MODE=1
T=$(mktemp -d /tmp/icotrans.XXXXXX); trap 'rm -rf "$T"' EXIT
D="$T/.icode_output/.icode_output_1"; mkdir -p "$D"
$CTL create --dir "$D" --ticket-id trans-1 --requirement r --birth plan >/dev/null

# 4) request_id 幂等重放（在链中合法状态上测，先独立 fixture）
R="$T/.icode_output/.icode_output_90"; mkdir -p "$R"
$CTL create --dir "$R" --ticket-id idem-1 --requirement r --birth plan >/dev/null
$CTL transition --dir "$R" --to plan_done --skip-gates --request-id req-x >/dev/null 2>&1
if $CTL transition --dir "$R" --to plan_done --skip-gates --request-id req-x 2>/dev/null | grep -q 'already_applied'; then
  ok "同 request_id 重放 → already_applied（不重复执行）"; else bad "request_id 幂等未生效"; fi

# 1) 合法全链（skip-gates 夹具模式）→ 全部通过
okchain=1
for st in plan_done review_in_progress review_done plan_finalized code_in_progress code_done deepcheck_in_progress deepcheck_done; do
  $CTL transition --dir "$D" --to "$st" --skip-gates >/dev/null 2>&1 || okchain=0
done
if [ "$okchain" -eq 1 ]; then ok "合法状态链全部通过"; else bad "合法状态链存在失败"; fi
if $CTL transition --dir "$D" --to completed --delivery-verdict verification_pending --skip-gates >/dev/null 2>&1; then
  ok "completed（显式 delivery-verdict）通过"; else bad "completed 显式 delivery-verdict 未通过"; fi

# 2) 非法跳转 → fail-closed
if $CTL transition --dir "$D" --to code_done --skip-gates 2>/dev/null | grep -q 'state_machine'; then
  ok "非法跳转被拒（fail-closed）"; else bad "非法跳转未被拒"; fi
if grep -q '"status": "code_done"' "$D/.ico_metadata.json"; then bad "非法跳转改了 status"; else ok "非法跳转后 status 未变"; fi

# 3) 未知目标状态 → exit 2 参数错误
$CTL transition --dir "$D" --to nope --skip-gates >/dev/null 2>&1; rc=$?
[ "$rc" -eq 2 ] && ok "未知目标状态 → exit 2" || bad "未知目标状态 rc=$rc 非 2"

# 5) legacy 工单 → 控制面拒绝变更（exit 3）提示迁移
LEG="$T/.icode_output/.icode_output_2"; mkdir -p "$LEG"
cat > "$LEG/.ico_metadata.json" <<'EOF'
{"ticket_id":"leg-1","requirement":"r","created_at":"2026-01-01T00:00:00","status":"init_in_progress","completed_steps":["0"]}
EOF
$CTL transition --dir "$LEG" --to code_in_progress >/dev/null 2>&1; rc=$?
[ "$rc" -eq 3 ] && ok "legacy transition → exit 3（只读适配提示迁移）" || bad "legacy transition rc=$rc 非 3"

# 6) legacy 迁移：dry-run 三分类 → apply（备份+事件）→ 二次幂等 already_v3
$CTL migration --dir "$LEG" >/dev/null 2>&1 && ok "migration dry-run 通过" || bad "migration dry-run 失败"
$CTL migration --dir "$LEG" --apply >/dev/null 2>&1 || { echo "  ❌ migration --apply 失败"; exit 1; }
[ -f "$LEG/.ico_metadata.json.pre-v3.bak" ] && ok "迁移生成 .pre-v3.bak 备份" || bad "迁移未备份"
if $CTL migration --dir "$LEG" --apply 2>/dev/null | grep -q 'already_v3'; then
  ok "二次迁移幂等（already_v3）"; else bad "二次迁移非幂等"; fi

# 7) 索引单一 writer：同 out_dir 不同 ticket_id → 拒绝（out_dir_ownership）
# 预置一个"入侵者"条目占住 D 的路径，再让 D（不同 ticket_id）写入 → 冲突拒绝
W2="$T/ws2"; D2="$W2/.icode_output/.icode_output_1"; mkdir -p "$D2"
$CTL create --dir "$D2" --ticket-id trans-1b --requirement r --birth plan >/dev/null
cat > "$T/idx.json" <<'EOF'
{"version":1,"updated_at":"2026-09-07T00:00:00","tickets":[
 {"control_schema_version":3,"ticket_id":"intruder","project_path":"__T__","out_dir":".icode_output/.icode_output_1","status":"completed"}
]}
EOF
sed -i "s|__T__|$W2|" "$T/idx.json"
if $CTL index-write --ticket-dir "$D2" --index "$T/idx.json" 2>/dev/null | grep -q 'out_dir_ownership'; then
  ok "同 out_dir 不同 ticket_id 拒绝写入"; else bad "out_dir 所有权冲突未拦截"; fi

# 8) snapshot 生成 + verify 一致（test_ticket_snapshot_resume）
$CTL snapshot --dir "$D2" >/dev/null 2>&1 || bad "snapshot 生成失败"
if $CTL snapshot --dir "$D2" --verify 2>/dev/null | grep -q '"ok": true'; then
  ok "snapshot --verify 一致"; else bad "snapshot --verify 不一致"; fi
if [ -f "$D2/ticket_snapshot.json" ]; then ok "ticket_snapshot.json 落盘"; else bad "snapshot 未落盘"; fi

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
