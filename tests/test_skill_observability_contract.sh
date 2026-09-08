#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

CTL="python3 tools/icode_control.py"
PROPOSE="tools/propose_skill_candidates.py"
export ICODE_CONTROL_TEST_MODE=1
TMP="$(mktemp -d /tmp/icode-skill-observe.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

make_ticket() {
  local n="$1"
  local dir="$TMP/.icode_output/.icode_output_$n"
  mkdir -p "$(dirname "$dir")"
  $CTL create --dir "$dir" --ticket-id "skill-run-$n" --requirement r --birth init >/dev/null
  printf '%s' "$dir"
}

record_gap() {
  local dir="$1" request="$2" finding="$3"
  $CTL record-skill-run --dir "$dir" --skill unrouted \
    --trigger 'cross-process-clock-drift' --result degraded --adopted false \
    --evidence-ref 'log:event-window' --elapsed-ms 1200 --estimated-tokens 850 \
    --unique-finding "$finding" --agent-id evaluator --request-id "$request"
}

D1="$(make_ticket 1)"
if record_gap "$D1" run-1 'clock jump segment' >/dev/null \
  && record_gap "$D1" run-1 'clock jump segment' | grep -q already_applied \
  && python3 - "$D1/.ico_metadata.json" <<'PY'
import json, sys
run = json.load(open(sys.argv[1], encoding="utf-8"))["extensions"]["skills"]["runs"][0]
for key in ("trigger", "result", "adopted", "evidence_refs", "elapsed_ms", "estimated_tokens", "unique_findings"):
    assert key in run
assert run["adopted"] is False and run["elapsed_ms"] == 1200
PY
then
  ok "skill run 原子记录、幂等且观测字段完整"
else
  bad "skill run 观测合同失败"
fi

D2="$(make_ticket 2)"
record_gap "$D2" run-2 'buffered timestamps' >/dev/null
if python3 "$PROPOSE" --root "$TMP" | python3 -c 'import json,sys; assert json.load(sys.stdin)["candidates"] == []'; then
  ok "同一模式不足三次不生成候选"
else
  bad "两次重复即误生成技能候选"
fi

D3="$(make_ticket 3)"
record_gap "$D3" run-3 'NTP correction evidence' >/dev/null
if python3 "$PROPOSE" --root "$TMP" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert len(d["candidates"])==1; assert d["candidates"][0]["trigger"]=="cross-process-clock-drift"; assert d["candidates"][0]["occurrences"]==3'; then
  ok "三次同模式只输出一个候选报告"
else
  bad "三次重复未生成稳定候选"
fi

if [ "$(find "$TMP" -name '*candidate*' -o -name 'SKILL.md' | wc -l)" -eq 0 ] \
  && $CTL validate --dir "$D1" --skip-linters | grep -q '"ok": true'; then
  ok "候选提炼只读且工单事件链有效"
else
  bad "候选工具写文件或破坏控制面"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
