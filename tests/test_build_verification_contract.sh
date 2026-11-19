#!/usr/bin/env bash
# Windows: python3 可能是 WindowsApps 假 stub（无输出、exit 49）；加载共享 shim
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/python3-shim.sh" 2>/dev/null || true
set -euo pipefail
cd "$(dirname "$0")/.."
export ICODE_CONTROL_TEST_MODE=1
TMP="$(mktemp -d "${TMPDIR:-/tmp}/icode-build-verify.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
D="$TMP/.icode_output/.icode_output_1"
CTL=(python3 tools/icode_control.py)
"${CTL[@]}" create --dir "$D" --ticket-id build-1 --requirement r --birth init \
  --metadata-json '{"verification_contract":{"required":true,"required_layers":["physical"],"required_consumers":["algorithm"],"required_scenarios":["normal"]}}' >/dev/null
cp "$D/.ico_metadata.json" "$TMP/before.json"
reject() {
  if "${CTL[@]}" record-verification --dir "$D" --kind build --outcome pass \
      --evidence 'build log' --request-id rejected "$@" >"$TMP/reject.json" 2>&1; then
    printf 'FAIL accepted invalid build record\n' >&2
    exit 1
  fi
  cmp "$D/.ico_metadata.json" "$TMP/before.json"
}
reject --build-source fresh --artifact-identity sha256:a
reject --build-source unknown --artifact-identity sha256:a --baseline HEAD:a
reject --build-source fresh --artifact-identity sha256:a --baseline HEAD:a --layer physical
reject --build-source fresh --artifact-identity sha256:a --baseline HEAD:a --device SN1
reject --build-source reused --artifact-identity sha256:a --baseline HEAD:a
"${CTL[@]}" record-verification --dir "$D" --kind build --outcome fail \
  --build-source fresh --evidence 'compiler exit=2; build log' --request-id failed >/dev/null
"${CTL[@]}" record-verification --dir "$D" --kind build --outcome pass \
  --build-source fresh --artifact-identity sha256:a --baseline HEAD:a \
  --consumer module_a --scenario incremental --evidence 'compiler exit=0; build log' \
  --request-id passed >/dev/null
"${CTL[@]}" record-verification --dir "$D" --kind build --outcome pass \
  --build-source fresh --artifact-identity sha256:a --baseline HEAD:a \
  --consumer module_a --scenario incremental --evidence 'compiler exit=0; build log' \
  --request-id passed >"$TMP/replay.json"
python3 - "$D/.ico_metadata.json" "$TMP/before.json" "$TMP/replay.json" <<'PY'
import json, pathlib, shutil, sys, tempfile
from tools.icode_control import check_schema, read_metadata_readonly

def load_logical(path):
    """metadata 可能是 storage v2 envelope；用控制面只读适配器还原逻辑对象。"""
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(path, pathlib.Path(tmp) / ".ico_metadata.json")
        return read_metadata_readonly(tmp)

meta = load_logical(sys.argv[1])
before = load_logical(sys.argv[2])
replay = json.load(open(sys.argv[3]))
assert len(meta["verification_runs"]) == 2
assert all(run["kind"] == "build" and run["layer"] == "build" for run in meta["verification_runs"])
assert meta["verification_runs"][-1]["outcome"] == "pass"
assert replay["already_applied"] is True
for key in ("status", "completed_steps", "patch_history", "delivery_verdict", "verification_contract"):
    assert meta.get(key) == before.get(key), key
schema = json.load(open("schemas/ticket-metadata.schema.json"))["properties"]["verification_runs"]["items"]
valid = meta["verification_runs"][-1]
assert not check_schema(valid, schema)
for changed in ({"layer": "physical"}, {"device": "SN1"}):
    assert check_schema({**valid, **changed}, schema)
assert check_schema({key: value for key, value in valid.items() if key != "layer"}, schema)
assert not check_schema({**valid, "kind": "listen", "layer": "physical", "device": "SN1"}, schema)
PY
python3 tools/lint_workflow_contract.py "$D" --step audit-verified --strict --json \
  >"$TMP/debt.json" 2>/dev/null && { printf 'FAIL build cleared physical debt\n'; exit 1; }
python3 - "$TMP/debt.json" <<'PY'
import json, sys
assert "physical" in json.dumps(json.load(open(sys.argv[1])))
PY
printf 'PASS build identity/scope/failure/idempotency/status/physical-debt gates\n'
