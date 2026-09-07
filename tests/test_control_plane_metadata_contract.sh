#!/usr/bin/env bash
# 控制面 metadata 契约（§10: test_ticket_metadata_schema_contract / test_completed_requires_all_gates_contract
#                       / test_debug_isolation_vnext / test_delivery_evidence_layers）
#
# 在 /tmp 隔离 fixture 上验证 vNext 工单 schema 严格性、completed 门禁、debug 隔离、交付分层。
set -u
cd "$(dirname "$0")/.." || exit 1
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

CTL="python3 tools/icode_control.py"
export ICODE_CONTROL_TEST_MODE=1
T=$(mktemp -d /tmp/icometa.XXXXXX); trap 'rm -rf "$T"' EXIT
D="$T/.icode_output/.icode_output_1"; mkdir -p "$D"

write_meta() { rm -f "$D/.ico_events.jsonl" "$D/.icontrol_txn.json"; cat > "$D/.ico_metadata.json"; }
reset_dir() { rm -f "$D/.ico_metadata.json" "$D/.ico_events.jsonl" "$D/.icontrol_txn.json"; }

# 1) 合法 vNext fixture → validate 通过
reset_dir
$CTL create --dir "$D" --ticket-id meta-1 --requirement r --birth plan >/dev/null
if $CTL validate --dir "$D" --skip-linters | grep -q '"ok": true'; then ok "合法 vNext fixture validate 通过"; else bad "合法 fixture 被误判失败"; fi

# 2) 顶层未登记字段 → metadata_schema 违例（fail-closed，实验字段须进 extensions）
write_meta <<'EOF'
{
  "schema_version": 3, "ticket_id": "meta-2", "requirement": "r", "created_at": "2026-09-07T00:00:00",
  "status": "init_in_progress", "completed_steps": [],
  "rogue_top_level_field": 1
}
EOF
if $CTL validate --dir "$D" --skip-linters | grep -q 'metadata_schema'; then ok "未登记顶层字段被拒（metadata_schema）"; else bad "未登记字段未拦截"; fi

# 3) 词表外 status → 违例
write_meta <<'EOF'
{
  "schema_version": 3, "ticket_id": "meta-3", "requirement": "r", "created_at": "2026-09-07T00:00:00",
  "status": "invented_status", "completed_steps": []
}
EOF
if $CTL validate --dir "$D" --skip-linters | grep -q 'metadata_schema'; then ok "词表外 status 被拒"; else bad "词表外 status 未拦截"; fi

# 4) debug 孪生（debug:true）→ index-write 拒绝（debug 隔离）
DD="$T/.icode_output/.debug/.icode_output_1"; mkdir -p "$DD"
$CTL create --dir "$DD" --ticket-id meta-debug --requirement r --birth debug-log >/dev/null
if $CTL index-write --ticket-dir "$DD" --index "$T/idx.json" 2>/dev/null | grep -q 'debug 孪生工单走 .debug 隔离域'; then
  ok "debug 孪生拒入全局索引（debug_isolation）"
else bad "debug 孪生未拦截索引"; fi

# 4b) risk_profile 是对象（对象/null），不得误标 string——历史 bug：schema 曾把 risk_profile 写成 string 导致合法工单 validate 误判违例
reset_dir
$CTL create --dir "$D" --ticket-id meta-rp --requirement r --birth plan --metadata-json \
  '{"semantic_decisions":[],"requirement_deltas":[],"impact_contract":{"identity_change":false},"risk_profile":{"requested_mode":"full","effective_mode":"full","triggers":[],"risk_flags":{},"override":false},"acceptance_contract":{"requires_lifecycle":false}}' >/dev/null
if $CTL validate --dir "$D" --skip-linters | grep -q '"ok": true'; then ok "risk_profile 对象合法（防 schema 误标 string 回归）"; else bad "risk_profile 对象被误判违例"; fi

# 5) completed 缺 delivery-verdict → 拒绝（交付分层：禁止缺省推断）
reset_dir
$CTL create --dir "$D" --ticket-id meta-5 --requirement r --birth plan >/dev/null
for st in plan_done review_in_progress review_done plan_finalized code_in_progress code_done deepcheck_in_progress deepcheck_done; do
  $CTL transition --dir "$D" --to "$st" --skip-gates >/dev/null || exit 1
done
if $CTL transition --dir "$D" --to completed --skip-gates 2>/dev/null | grep -q 'delivery-verdict'; then
  ok "completed 缺 delivery-verdict 被拒（delivery_evidence_layer）"; else bad "completed 缺 delivery-verdict 未拦截"; fi

# 6) completed 带 delivery-verdict 但门禁失败（无 trace）→ fail-closed 状态不前移
if $CTL transition --dir "$D" --to completed --delivery-verdict verification_pending 2>/dev/null \
    | grep -q '门禁未通过'; then
  ok "completed 门禁未过 → fail-closed 状态不前移（completed_requires_all_gates）"
else bad "completed 未跑门禁即通过"; fi
if grep -q '"status": "completed"' "$D/.ico_metadata.json" 2>/dev/null; then
  bad "门禁失败后 status 仍被前移"; else ok "门禁失败后 status 保持原值"; fi

# 7) schema 文件本身可被外部 jsonschema 消费（draft-07 合法）
if python3 - <<'PY' 2>/dev/null
import json, jsonschema
for f in ["ticket-metadata.schema.json","ticket-event.schema.json","ticket-index.schema.json"]:
    jsonschema.Draft7Validator.check_schema(json.load(open("schemas/"+f)))
print("ok")
PY
then ok "三 schema 均为合法 draft-07（可被 jsonschema 消费）"; else bad "schema 非合法 draft-07"; fi

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
