#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

CTL="python3 tools/icode_control.py"
export ICODE_CONTROL_TEST_MODE=1
TMP="$(mktemp -d /tmp/icode-claims.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
D="$TMP/.icode_output/.icode_output_1"
mkdir -p "$(dirname "$D")"
$CTL create --dir "$D" --ticket-id claim-1 --requirement r --birth init >/dev/null

if $CTL record-claim --dir "$D" --kind fact --statement observed \
    --source 'raw.log:10' --boundary 'proves receipt only' \
    --evidence 'raw.log:10 payload=x' --request-id claim-fact-1 >/dev/null \
  && $CTL record-claim --dir "$D" --kind fact --statement observed \
    --source 'raw.log:10' --boundary 'proves receipt only' \
    --evidence 'raw.log:10 payload=x' --request-id claim-fact-1 \
      | grep -q already_applied; then
  ok "record-claim 原子追加且同 request_id 幂等"
else
  bad "record-claim 原子/幂等失败"
fi

if $CTL record-claim --dir "$D" --kind fact --statement bad \
    --source source --boundary boundary 2>/dev/null | grep -q claim_evidence_required \
  && $CTL record-claim --dir "$D" --kind inference --statement guess \
    --source 'code candidate' --boundary 'runtime unknown' \
    --next-action 'map runtime commit' --request-id claim-inference-1 >/dev/null \
  && $CTL record-claim --dir "$D" --kind unobserved --statement 'NAV consumption' \
    --source 'missing NAV observation' --boundary 'not a failure proof' \
    --next-action 'inspect ingress' --request-id claim-unobserved-1 >/dev/null \
  && $CTL record-claim --dir "$D" --kind refuted --statement 'Broker is root cause' \
    --source 'raw.log:10' --boundary 'only a rejection point' \
    --evidence 'handler=false' --contradicted-by 'runtime version unknown' \
    --request-id claim-refuted-1 >/dev/null; then
  ok "fact 强制证据且 inference/unobserved/refuted 词表可写"
else
  bad "claim kind/证据边界契约失败"
fi

if $CTL record-claim --dir "$D" --kind fact --statement missing-source \
    --source '' --boundary boundary --evidence evidence 2>/dev/null | grep -q claim_source_boundary \
  && $CTL record-claim --dir "$D" --kind fact --statement missing-boundary \
    --source source --boundary '' --evidence evidence 2>/dev/null | grep -q claim_source_boundary; then
  ok "source 与 boundary 不能为空"
else
  bad "空 source/boundary 未被拒绝"
fi

if $CTL metadata-update --dir "$D" --set-json '{"claims":[]}' 2>/dev/null \
    | grep -q metadata_update_protected \
  && $CTL event --dir "$D" --type claim_recorded --payload '{}' 2>/dev/null \
    | grep -q reserved_event_type; then
  ok "通用 writer 不能绕过 claims 专用入口"
else
  bad "claims 可被通用 writer 绕过"
fi

if $CTL validate --dir "$D" --skip-linters | grep -q '"ok": true' \
  && python3 - "$D/.ico_metadata.json" "$D/.ico_events.jsonl" <<'PY'
import json, sys
meta = json.load(open(sys.argv[1], encoding="utf-8"))
events = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
claims = meta["claims"]
event_claims = [
    {k: v for k, v in event["payload"].items() if k != "metadata_hash_after"}
    for event in events if event["event_type"] == "claim_recorded"
]
assert claims == event_claims
assert len(claims) == 4
assert [item["status"] for item in claims] == ["supported", "open", "open", "refuted"]
PY
then
  ok "claims 与 claim_recorded 事件链一致"
else
  bad "claims schema/事件一致性失败"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
