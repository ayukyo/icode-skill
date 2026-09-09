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
TMP="$(mktemp -d -t icode-embedded-verification.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
D="$TMP/.icode_output/.icode_output_1"
mkdir -p "$(dirname "$D")"

BASELINE_REF="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CONTRACT='{"impact_contract":null,"acceptance_contract":null,"verification_contract":{"required":true,"profile":"camera","baseline_ref":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","required_layers":["physical"],"required_consumers":["camera_pipeline"],"required_scenarios":["stream"],"required_cells":[{"layer":"physical","consumer":"camera_pipeline","scenario":"stream"}],"required_metrics":[{"name":"fps","unit":"fps","operator":"gte","value":29.0,"layer":"physical","consumer":"camera_pipeline","scenario":"stream"}]}}'
if $CTL create --dir "$D" --ticket-id embedded-verify-1 --requirement r --birth init \
    --metadata-json "$CONTRACT" >/dev/null 2>&1; then
  ok "schema 接受 camera profile 和指标合同"
else
  bad "schema 尚不接受 camera profile 和指标合同"
fi

if $CTL record-verification --dir "$D" --kind device_test --outcome pass \
    --layer physical --consumer camera_pipeline --scenario stream \
    --profile camera --baseline-ref "$BASELINE_REF" \
    --baseline 'device=cam-001 artifact=sha256:a' --evidence 'stream trace' \
    --request-id metric-missing >/dev/null 2>&1 \
  && $LINT "$D" --step audit-verified --strict --json 2>&1 | rg -q 'metric.*fps|指标.*fps'; then
  ok "缺失指标阻断 camera verified"
else
  bad "缺失指标未被精确阻断"
fi

if $CTL record-verification --dir "$D" --kind device_test --outcome pass \
    --layer physical --consumer camera_pipeline --scenario stream \
    --profile camera --baseline-ref "$BASELINE_REF" --metrics-json '{"fps":28.5}' \
    --baseline 'device=cam-001 artifact=sha256:a' --evidence 'stream trace below budget' \
    --request-id metric-low >/dev/null 2>&1 \
  && $LINT "$D" --step audit-verified --strict --json 2>&1 | rg -q '28.5.*gte.*29|fps'; then
  ok "未达阈值指标阻断 verified"
else
  bad "未达阈值指标未阻断"
fi

if $CTL record-verification --dir "$D" --kind device_test --outcome pass \
    --layer physical --consumer camera_pipeline --scenario stream \
    --profile camera --baseline-ref "$BASELINE_REF" --metrics-json '{"fps":29.0}' \
    --baseline 'device=cam-001 artifact=sha256:a' --evidence 'stream trace at budget' \
    --request-id metric-pass >/dev/null 2>&1 \
  && $LINT "$D" --step audit-verified --strict --json >/dev/null 2>&1; then
  ok "等于 gte 阈值的最新指标允许 verified gate"
else
  bad "满足指标合同仍被阻断"
fi

if $CTL record-verification --dir "$D" --kind device_test --outcome pass \
    --layer physical --consumer camera_pipeline --scenario stream \
    --profile embedded --baseline-ref "$BASELINE_REF" --metrics-json '{"fps":30}' \
    --baseline 'device=cam-001 artifact=sha256:a' --evidence 'wrong profile' \
    --request-id profile-mismatch >/dev/null 2>&1 \
  && $LINT "$D" --step audit-verified --strict --json 2>&1 | rg -q 'profile'; then
  ok "profile 不匹配阻断 verified"
else
  bad "profile 不匹配未阻断"
fi

if $CTL record-verification --dir "$D" --kind device_test --outcome pass \
    --layer physical --consumer camera_pipeline --scenario stream \
    --profile camera --baseline-ref 'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' \
    --metrics-json '{"fps":30}' --baseline 'device=cam-001 artifact=sha256:a' \
    --evidence 'wrong baseline digest' --request-id baseline-mismatch >/dev/null 2>&1 \
  && $LINT "$D" --step audit-verified --strict --json 2>&1 | rg -q 'baseline_ref.*期望'; then
  ok "baseline_ref 摘要不匹配阻断 verified"
else
  bad "baseline_ref 仅做非空检查"
fi

if $CTL record-verification --dir "$D" --kind device_test --outcome pass \
    --layer physical --consumer camera_pipeline --scenario stream \
    --profile camera --metrics-json '{"fps":30}' \
    --baseline 'device=cam-001 artifact=sha256:a' --evidence 'missing baseline ref' \
    --request-id baseline-missing >/dev/null 2>&1 \
  && $LINT "$D" --step audit-verified --strict --json 2>&1 | rg -q 'baseline_ref'; then
  ok "结构化 baseline_ref 缺失阻断 verified"
else
  bad "baseline_ref 缺失未阻断"
fi

D3="$TMP/.icode_output/.icode_output_3"
if $CTL create --dir "$D3" --ticket-id embedded-verify-3 --requirement r --birth init \
    --metadata-json "$CONTRACT" >/dev/null 2>&1 \
  && $CTL record-verification --dir "$D3" --kind device_test --outcome pass \
    --layer physical --consumer camera_pipeline --scenario stream \
    --profile camera --baseline-ref "$BASELINE_REF" --metrics-json '{"fps":28.5}' \
    --baseline 'device=cam-001 artifact=sha256:a' --evidence 'stream trace below budget' \
    --request-id debt-low >/dev/null 2>&1 \
  && python3 tools/verification_debt.py plan --ticket-dir "$D3" \
    --output "$D3/plan.json" --markdown "$D3/plan.md" >/dev/null 2>&1 \
  && python3 - "$D3/plan.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["summary"]["pending_units"] == 1
a=p["actions"][0]
assert "metric" in a["current_state"]
assert a["required_metrics"][0]["name"] == "fps"
assert "--profile" in a["record_command"]
assert "--baseline-ref" in a["record_command"]
assert "--metrics-json" in a["record_command"]
PY
then
  ok "验证债务计划识别 profile/metric 缺口"
else
  bad "验证债务计划误把低指标记录当作已满足"
fi

D5="$TMP/.icode_output/.icode_output_5"
SPARSE='{"impact_contract":null,"acceptance_contract":null,"verification_contract":{"required":true,"profile":"embedded","baseline_ref":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","required_layers":["physical","consumption"],"required_consumers":["sensor","algorithm"],"required_scenarios":["stream","restart"],"required_cells":[{"layer":"physical","consumer":"sensor","scenario":"stream"},{"layer":"consumption","consumer":"algorithm","scenario":"restart"}]}}'
if $CTL create --dir "$D5" --ticket-id embedded-sparse --requirement r --birth init \
    --metadata-json "$SPARSE" >/dev/null 2>&1 \
  && $CTL record-verification --dir "$D5" --kind device_test --outcome pass \
    --layer physical --consumer sensor --scenario stream --profile embedded \
    --baseline-ref "$BASELINE_REF" --baseline b --evidence e >/dev/null 2>&1 \
  && $CTL record-verification --dir "$D5" --kind device_test --outcome pass \
    --layer consumption --consumer algorithm --scenario restart --profile embedded \
    --baseline-ref "$BASELINE_REF" --baseline b --evidence e >/dev/null 2>&1 \
  && $LINT "$D5" --step audit-verified --strict --json >/dev/null 2>&1; then
  ok "required_cells 避免稀疏场景被展开成笛卡尔积"
else
  bad "required_cells 稀疏合同仍产生虚假验证债务"
fi

D8="$TMP/.icode_output/.icode_output_8"
IDENTITY_ONLY='{"impact_contract":null,"acceptance_contract":null,"verification_contract":{"required":true,"profile":"camera","baseline_ref":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","required_layers":["physical"],"required_consumers":["camera_pipeline"],"required_scenarios":["bringup"],"required_cells":[{"layer":"physical","consumer":"camera_pipeline","scenario":"bringup"}],"required_metrics":[]}}'
if $CTL create --dir "$D8" --ticket-id embedded-identity-only --requirement r --birth init \
    --metadata-json "$IDENTITY_ONLY" >/dev/null 2>&1 \
  && $CTL record-verification --dir "$D8" --kind device_test --outcome pass \
    --layer physical --consumer camera_pipeline --scenario bringup \
    --baseline b --evidence e >/dev/null 2>&1 \
  && $LINT "$D8" --step audit-verified --strict --json 2>&1 | rg -q 'profile|baseline_ref' \
  && python3 tools/verification_debt.py plan --ticket-dir "$D8" \
    --output "$D8/identity-plan.json" --markdown "$D8/identity-plan.md" >/dev/null 2>&1 \
  && python3 - "$D8/identity-plan.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["summary"]["pending_units"] == 1
a=p["actions"][0]
assert "--profile" in a["record_command"]
assert "--baseline-ref" in a["record_command"]
assert "--metrics-json" not in a["record_command"]
PY
then
  ok "无量化指标的 camera 单元仍强制 profile 与 baseline 摘要"
else
  bad "无量化指标可绕过 profile/baseline 身份门禁"
fi

D6="$TMP/.icode_output/.icode_output_6"
OVERLAY="$D6/embedded-plan.json"
if $CTL create --dir "$D6" --ticket-id embedded-overlay --requirement r --birth init \
    --metadata-json '{"impact_contract":null,"acceptance_contract":null}' >/dev/null 2>&1; then
  python3 - "$CONTRACT" "$OVERLAY" <<'PY'
import json, sys
d=json.loads(sys.argv[1])
json.dump({"verification_contract": d["verification_contract"]},
          open(sys.argv[2], "w", encoding="utf-8"))
PY
fi
if python3 tools/verification_debt.py plan --ticket-dir "$D6" --contract-file "$OVERLAY" \
    --output "$D6/overlay-plan.json" --markdown "$D6/overlay-plan.md" >/dev/null 2>&1 \
  && python3 - "$D6/overlay-plan.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["contract_source"].endswith("embedded-plan.json")
assert p["summary"]["required_units"] == 1
assert p["summary"]["pending_units"] == 1
PY
then
  ok "只读验证计划可消费 embedded profile 合同 overlay"
else
  bad "embedded profile 合同未接入验证债务计划"
fi

SET_CONTRACT="$(python3 - "$OVERLAY" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({"verification_contract": d["verification_contract"]}, separators=(",", ":")))
PY
)"
if $CTL metadata-update --dir "$D6" --set-json "$SET_CONTRACT" \
    --request-id register-embedded-contract >/dev/null 2>&1 \
  && python3 - "$D6/.ico_metadata.json" "$BASELINE_REF" <<'PY'
import json, sys
c=json.load(open(sys.argv[1], encoding="utf-8"))["verification_contract"]
assert c["profile"] == "camera"
assert c["baseline_ref"] == sys.argv[2]
assert len(c["required_cells"]) == 1
PY
then
  ok "执行验证前可经受控 writer 登记 profile 合同"
else
  bad "profile 合同无法接入执行态 metadata"
fi

D7="$TMP/.icode_output/.icode_output_7"
NONFINITE='{"impact_contract":null,"acceptance_contract":null,"verification_contract":{"required":true,"profile":"camera","required_layers":["physical"],"required_consumers":["camera_pipeline"],"required_scenarios":["stream"],"required_metrics":[{"name":"fps","unit":"fps","operator":"gte","value":NaN,"layer":"physical","consumer":"camera_pipeline","scenario":"stream"}]}}'
EVENTS_BEFORE="$(wc -l < "$D3/.ico_events.jsonl")"
if ! $CTL create --dir "$D7" --ticket-id embedded-nan --requirement r --birth init \
      --metadata-json "$NONFINITE" >/dev/null 2>&1 \
  && [[ ! -e "$D7/.ico_metadata.json" ]] \
  && ! $CTL metadata-update --dir "$D3" --set-json '{"test_timeout":Infinity}' \
      >/dev/null 2>&1 \
  && [[ "$EVENTS_BEFORE" == "$(wc -l < "$D3/.ico_events.jsonl")" ]]; then
  ok "所有控制面 JSON 输入拒绝 NaN/Infinity 且不落盘"
else
  bad "非有限数可进入控制面合同"
fi

D4="$TMP/.icode_output/.icode_output_4"
cp -a "$D3" "$D4"
python3 - "$D4/.ico_metadata.json" <<'PY'
import json, sys
p=sys.argv[1]
d=json.load(open(p, encoding="utf-8"))
d["ticket_id"]="embedded-verify-malformed"
d["verification_contract"]["required_metrics"][0]["name"]=[]
json.dump(d, open(p, "w", encoding="utf-8"))
PY
if $LINT "$D4" --step audit-verified --strict --json 2>&1 \
    | rg -q 'required_metrics.*name' \
  && python3 tools/verification_debt.py plan --ticket-dir "$D4" \
    --output "$D4/malformed-plan.json" --markdown "$D4/malformed-plan.md" \
    >/dev/null 2>&1 \
  && python3 - "$D4/malformed-plan.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["tracking_status"] == "invalid_contract"
PY
then
  ok "畸形 metric 合同 fail-closed 且只读工具不崩溃"
else
  bad "畸形 metric 合同触发异常或被误接受"
fi

RUNS_BEFORE="$(python3 - "$D/.ico_metadata.json" <<'PY'
import json,sys
print(len(json.load(open(sys.argv[1], encoding="utf-8"))["verification_runs"]))
PY
)"
INVALID_OK=1
for metrics in '[]' '{"fps":true}' '{"fps":NaN}' '{"fps":Infinity}'; do
  $CTL record-verification --dir "$D" --kind device_test --outcome pass \
      --layer physical --consumer camera_pipeline --scenario stream \
      --profile camera --baseline-ref embedded_baseline.json --metrics-json "$metrics" \
      --baseline b --evidence e >/dev/null 2>&1 && INVALID_OK=0
done
RUNS_AFTER="$(python3 - "$D/.ico_metadata.json" <<'PY'
import json,sys
print(len(json.load(open(sys.argv[1], encoding="utf-8"))["verification_runs"]))
PY
)"
if [[ "$INVALID_OK" -eq 1 && "$RUNS_BEFORE" == "$RUNS_AFTER" ]]; then
  ok "非法 metrics 在加锁写入前被拒绝且零副作用"
else
  bad "非法 metrics 校验或副作用保护失败"
fi

D2="$TMP/.icode_output/.icode_output_2"
GENERIC='{"impact_contract":null,"acceptance_contract":null,"verification_contract":{"required":true,"required_layers":["host"],"required_consumers":["tool"],"required_scenarios":["nominal"]}}'
if $CTL create --dir "$D2" --ticket-id embedded-verify-2 --requirement r --birth init \
    --metadata-json "$GENERIC" >/dev/null 2>&1 \
  && $CTL record-verification --dir "$D2" --kind device_test --outcome pass \
    --layer host --consumer tool --scenario nominal --baseline commit=abc \
    --evidence 'host contract output' >/dev/null 2>&1 \
  && $LINT "$D2" --step audit-verified --strict --json >/dev/null 2>&1; then
  ok "旧 generic 无指标合同保持兼容"
else
  bad "旧 generic 验证合同发生回归"
fi

if python3 - schemas/ticket-metadata.schema.json <<'PY'
import json,sys
from jsonschema import ValidationError, validate
s=json.load(open(sys.argv[1], encoding="utf-8"))
c=s["properties"]["verification_contract"]["properties"]
r=s["properties"]["verification_runs"]["items"]["properties"]
assert {"profile","baseline_ref","required_cells","required_metrics"} <= set(c)
assert {"profile","baseline_ref","metrics"} <= set(r)
contract_schema=s["properties"]["verification_contract"]
invalid={
    "required": True,
    "profile": "embedded",
    "required_layers": ["physical"],
    "required_consumers": ["sensor"],
    "required_scenarios": ["bringup"],
}
try:
    validate(invalid, contract_schema)
except ValidationError:
    pass
else:
    raise AssertionError("embedded profile without baseline_ref must be rejected")
PY
then ok "schema 暴露字段并拒绝无摘要 embedded 合同"; else bad "schema 字段或摘要条件缺失"; fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
