#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

CTL="python3 tools/icode_control.py"
LINT="python3 tools/lint_workflow_contract.py"
export ICODE_CONTROL_TEST_MODE=1
TMP="$(mktemp -d /tmp/icode-layered-verify.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
D="$TMP/.icode_output/.icode_output_1"
mkdir -p "$(dirname "$D")"

CONTRACT='{"impact_contract":null,"acceptance_contract":null,"verification_contract":{"required":true,"required_layers":["delivery","consumption"],"required_consumers":["algorithm"],"required_scenarios":["app_closed"]}}'
$CTL create --dir "$D" --ticket-id layered-1 --requirement r --birth init \
  --metadata-json "$CONTRACT" >/dev/null

if $LINT "$D" --step audit-verified --strict --json 2>/dev/null \
    | grep -q delivery_evidence; then
  ok "required contract 缺验证记录时阻断 verified"
else
  bad "缺验证记录未阻断 verified"
fi

if $CTL record-verification --dir "$D" --kind listen --outcome pass \
    --layer delivery --consumer algorithm --scenario app_closed \
    --baseline 'device=SN1 artifact=sha256:a' --evidence 'ipc receive id=7' \
    --request-id layer-delivery >/dev/null \
  && $LINT "$D" --step audit-verified --strict --json 2>/dev/null \
      | grep -q 'layer=consumption'; then
  ok "单层 pass 不能覆盖缺失 required layer"
else
  bad "缺失 required layer 未被精确报告"
fi

if $CTL record-verification --dir "$D" --kind device_test --outcome inconclusive \
    --layer consumption --consumer algorithm --scenario app_closed \
    --baseline 'device=SN1 artifact=sha256:a' --evidence 'consumer log missing' \
    --request-id layer-consume-unknown >/dev/null \
  && $LINT "$D" --step audit-verified --strict --json 2>/dev/null \
      | grep -q 'inconclusive'; then
  ok "required cell inconclusive 阻断 verified"
else
  bad "inconclusive 未阻断 verified"
fi

if $CTL record-verification --dir "$D" --kind device_test --outcome pass \
    --layer consumption --consumer algorithm --scenario app_closed \
    --baseline 'device=SN1 artifact=sha256:a' --evidence 'consumer applied id=7' \
    --request-id layer-consume-pass >/dev/null \
  && $LINT "$D" --step audit-verified --strict --json >/dev/null; then
  ok "每个 required cell 最新记录均 pass 时允许 verified gate"
else
  bad "完整分层验证仍被阻断"
fi

if python3 - "$D/.ico_metadata.json" <<'PY'
import json, sys
meta = json.load(open(sys.argv[1], encoding="utf-8"))
runs = meta["verification_runs"]
assert runs[-1]["layer"] == "consumption"
assert runs[-1]["consumer"] == "algorithm"
assert runs[-1]["scenario"] == "app_closed"
assert runs[-1]["baseline"] == "device=SN1 artifact=sha256:a"
PY
then
  ok "record-verification 保存 layer/consumer/scenario/baseline"
else
  bad "分层验证字段未写入"
fi

D2="$TMP/.icode_output/.icode_output_2"
$CTL create --dir "$D2" --ticket-id layered-2 --requirement r --birth init \
  --metadata-json '{"impact_contract":null,"acceptance_contract":null,"verification_contract":{"required":false,"required_layers":[],"required_consumers":[],"required_scenarios":[]}}' >/dev/null
if $LINT "$D2" --step audit-verified --strict --json >/dev/null; then
  ok "required=false 的非现场任务保持兼容"
else
  bad "非现场任务被无条件要求分层验证"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
