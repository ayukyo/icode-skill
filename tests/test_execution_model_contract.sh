#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONTROL="$ROOT/tools/icode_control.py"
TMP=$(mktemp -d /tmp/icode-execution-model.XXXXXX)
trap 'rm -rf "$TMP"' EXIT
export ICODE_CONTROL_TEST_MODE=1

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

json_assert() {
  local expression=$1
  python3 -c "import json,sys; data=json.load(sys.stdin); assert $expression" \
    || fail "JSON assertion failed: $expression"
}

python3 - "$ROOT" <<'PY'
import importlib.util
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "lint_workflow_contract", root / "tools" / "lint_workflow_contract.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
catalog = json.loads((root / "mcp" / "workflow-gate" / "gates.json").read_text())
issues = module.validate_execution_model_catalog(catalog)
assert not issues, issues
assert {"plan", "review", "merge", "code", "deepcheck", "audit", "patch", "verify"} <= set(
    catalog["execution_model"]["step_contracts"]
)
PY

WORK="$TMP/workspace"
TICKET="$WORK/.icode_output/.icode_output_1"
mkdir -p "$TICKET"
git -C "$WORK" init -q
git -C "$WORK" config user.email test@example.invalid
git -C "$WORK" config user.name 'ICODE Test'
printf 'base\n' > "$WORK/source.txt"
git -C "$WORK" add source.txt
git -C "$WORK" commit -qm base

python3 "$CONTROL" create --dir "$TICKET" --ticket-id execution-1 \
  --requirement 'implement execution model' --birth plan --request create-1 >/dev/null

START=$(python3 "$CONTROL" step --dir "$TICKET" --step plan --phase start \
  --attempt plan-a --request plan-start-a)
printf '%s' "$START" | json_assert 'data["attempt"] == "plan-a" and data["required_checks"] == ["before_write", "before_transition"]'

python3 "$CONTROL" step --dir "$TICKET" --step plan --phase check \
  --attempt plan-a --boundary before_write --request plan-check-write \
  | json_assert 'data["ok"] and data["result"] == "pass"'

printf '# plan\n' > "$TICKET/01_plan.md"
ARTIFACT=$(python3 "$CONTROL" artifact --dir "$TICKET" --step plan --attempt plan-a \
  --path 01_plan.md --scope ticket --request plan-artifact)
printf '%s' "$ARTIFACT" | json_assert 'data["output"] == "plan" and len(data["sha256"]) == 64'
python3 "$CONTROL" artifact --dir "$TICKET" --step plan --attempt plan-a \
  --path 01_plan.md --scope ticket --request plan-artifact \
  | json_assert 'data["already_applied"] is True'

printf 'wrong\n' > "$TICKET/not-the-plan.md"
if python3 "$CONTROL" artifact --dir "$TICKET" --step plan --attempt plan-a \
    --path not-the-plan.md --scope ticket --request wrong-artifact >/dev/null; then
  fail 'undeclared output was accepted'
fi

python3 "$CONTROL" step --dir "$TICKET" --step plan --phase check \
  --attempt plan-a --boundary before_transition --request plan-check-transition \
  | json_assert 'data["ok"] and data["route"] == "continue"'
python3 "$CONTROL" step --dir "$TICKET" --step plan --phase finish \
  --attempt plan-a --outcome success --evidence 01_plan.md --request plan-finish-a \
  | json_assert 'data["ok"] and data["outcome"] == "success"'

python3 "$CONTROL" transition --dir "$TICKET" --to plan_done \
  --skip-gates --request transition-plan >/dev/null
python3 "$CONTROL" transition --dir "$TICKET" --to review_in_progress \
  --skip-gates --request transition-review-start >/dev/null

python3 "$CONTROL" step --dir "$TICKET" --step review --phase start \
  --attempt review-a --request review-start-a >/dev/null
python3 "$CONTROL" step --dir "$TICKET" --step review --phase check \
  --attempt review-a --boundary before_write --request review-check-write >/dev/null
printf '# plan changed while reviewer waited\n' >> "$TICKET/01_plan.md"
set +e
DRIFT=$(python3 "$CONTROL" step --dir "$TICKET" --step review --phase check \
  --attempt review-a --boundary after_wait --request review-check-wait)
DRIFT_RC=$?
set -e
[[ "$DRIFT_RC" -eq 1 ]] || fail 'protected input drift did not block'
printf '%s' "$DRIFT" | json_assert 'not data["ok"] and data["result"] == "blocked" and data["route"] == "review" and "plan" in data["changed_inputs"]'
python3 "$CONTROL" step --dir "$TICKET" --step review --phase finish \
  --attempt review-a --outcome blocked --evidence input-drift --request review-finish-a >/dev/null

python3 "$CONTROL" step --dir "$TICKET" --step review --phase start \
  --attempt review-b --request review-start-b >/dev/null
if python3 "$CONTROL" step --dir "$TICKET" --step review --phase finish \
    --attempt review-b --outcome success --evidence premature --request review-finish-b >/dev/null; then
  fail 'step success without required checks/outputs was accepted'
fi
python3 "$CONTROL" step --dir "$TICKET" --step review --phase finish \
  --attempt review-b --outcome blocked --evidence contract-test --request review-block-b >/dev/null

python3 "$CONTROL" operation --dir "$TICKET" --phase start --name deploy-image \
  --opclass external_side_effect --attempt deploy-a --input image-v1 --request deploy-start-a >/dev/null
if python3 "$CONTROL" operation --dir "$TICKET" --phase start --name deploy-image \
    --opclass external_side_effect --attempt deploy-b --input image-v1 --request deploy-start-b >/dev/null; then
  fail 'ambiguous side-effect replay was accepted'
fi
python3 "$CONTROL" operation --dir "$TICKET" --phase finish --attempt deploy-a \
  --outcome failure --failure ambiguous_side_effect --evidence 'device:unknown' \
  --check 'no receipt; inspected target' --request deploy-finish-a \
  | json_assert 'data["decision"]["action"] == "verify_receipt" and not data["decision"]["auto_retry"]'
python3 "$CONTROL" operation --dir "$TICKET" --phase start --name deploy-image \
  --opclass external_side_effect --attempt deploy-b --input image-v1 --request deploy-start-b >/dev/null
python3 "$CONTROL" operation --dir "$TICKET" --phase finish --attempt deploy-b \
  --outcome success --evidence 'device:version=v1' --check 'target version matches' \
  --request deploy-finish-b >/dev/null
python3 "$CONTROL" operation --dir "$TICKET" --phase finish --attempt deploy-b \
  --outcome success --evidence 'device:version=v1' --check 'target version matches' \
  --request deploy-finish-b \
  | json_assert 'data["already_applied"] is True'

python3 "$CONTROL" step --dir "$TICKET" --step verify --phase start \
  --attempt verify-a --request verify-start-a >/dev/null
python3 "$CONTROL" step --dir "$TICKET" --step verify --phase check \
  --attempt verify-a --boundary before_side_effect --request verify-check-side-effect >/dev/null
python3 "$CONTROL" record-verification --dir "$TICKET" --kind device_test \
  --outcome pass --evidence 'fixture:device-pass' --request verify-record-a >/dev/null
python3 "$CONTROL" step --dir "$TICKET" --step verify --phase check \
  --attempt verify-a --boundary after_wait --request verify-check-wait >/dev/null
python3 "$CONTROL" step --dir "$TICKET" --step verify --phase finish \
  --attempt verify-a --outcome success --evidence verification_recorded \
  --request verify-finish-a \
  | json_assert 'data["ok"] and data["outcome"] == "success"'

if python3 "$CONTROL" operation --dir "$TICKET" --phase start --name write-index \
    --opclass managed_write --attempt write-a --input index-v1 >/dev/null; then
  fail 'managed write without idempotency key was accepted'
fi

python3 "$CONTROL" policy --opclass read_only --failure retryable_transport --attempts 0 \
  | json_assert 'data["action"] == "retry" and data["auto_retry"] is True and data["backoff_seconds"] == 0'
python3 "$CONTROL" policy --opclass external_side_effect --failure retryable_transport --attempts 0 \
  | json_assert 'data["action"] == "verify_receipt" and data["auto_retry"] is False'
python3 "$CONTROL" policy --opclass destructive_hardware --failure capability_unavailable --attempts 0 \
  | json_assert 'data["action"] == "human_decision" and data["auto_retry"] is False'
python3 "$CONTROL" policy --opclass read_only --failure retryable_transport --attempts 3 \
  | json_assert 'data["action"] == "block" and data["reason"] == "retry_budget_exhausted"'

TRACE=$(python3 "$CONTROL" trace --dir "$TICKET" --limit 100)
printf '%s' "$TRACE" | json_assert 'any(item["type"] == "gate_checked" and item.get("result") == "blocked" for item in data["events"]) and not data["open_operations"]'

python3 "$CONTROL" snapshot --dir "$TICKET" >/dev/null
python3 "$CONTROL" snapshot --dir "$TICKET" --verify \
  | json_assert 'data["ok"] is True'
python3 - "$TICKET/ticket_snapshot.json" <<'PY'
import json
import sys
snapshot = json.load(open(sys.argv[1], encoding="utf-8"))
assert "execution" in snapshot
assert "open_steps" in snapshot["execution"]
assert "open_operations" in snapshot["execution"]
PY

if python3 "$CONTROL" event --dir "$TICKET" --type step_finished \
    --payload '{}' --request forged-step >/dev/null; then
  fail 'generic event forged a reserved execution event'
fi

# 无 execution_model 事件的现有工单保持可推进；但开放的副作用动作仍会阻断完成态。
TICKET2="$WORK/.icode_output/.icode_output_2"
mkdir -p "$TICKET2"
python3 "$CONTROL" create --dir "$TICKET2" --ticket-id execution-legacy-compatible \
  --requirement 'compatibility fixture' --birth plan --request create-2 >/dev/null
python3 "$CONTROL" transition --dir "$TICKET2" --to plan_done \
  --skip-gates --request ticket2-plan >/dev/null
python3 "$CONTROL" transition --dir "$TICKET2" --to review_in_progress \
  --skip-gates --request ticket2-review-start >/dev/null
python3 "$CONTROL" operation --dir "$TICKET2" --phase start --name remote-merge \
  --opclass external_side_effect --attempt merge-a --input main --request merge-start-a >/dev/null
if python3 "$CONTROL" transition --dir "$TICKET2" --to review_done \
    --skip-gates --request ticket2-review-done >/dev/null; then
  fail 'transition ignored an open side-effect operation'
fi
python3 "$CONTROL" operation --dir "$TICKET2" --phase finish --attempt merge-a \
  --outcome success --evidence 'git:merge-result' --check 'working tree inspected' \
  --request merge-finish-a >/dev/null
python3 "$CONTROL" transition --dir "$TICKET2" --to review_done \
  --skip-gates --request ticket2-review-done >/dev/null

# 预先存在的输出不能冒充本 attempt 新产物，必须补 artifact receipt。
TICKET3="$WORK/.icode_output/.icode_output_3"
mkdir -p "$TICKET3"
python3 "$CONTROL" create --dir "$TICKET3" --ticket-id execution-output-receipt \
  --requirement 'output receipt fixture' --birth plan --request create-3 >/dev/null
printf '# preexisting plan\n' > "$TICKET3/01_plan.md"
python3 "$CONTROL" step --dir "$TICKET3" --step plan --phase start \
  --attempt plan-preexisting --request plan-preexisting-start >/dev/null
python3 "$CONTROL" step --dir "$TICKET3" --step plan --phase check \
  --attempt plan-preexisting --boundary before_write --request plan-preexisting-write >/dev/null
python3 "$CONTROL" step --dir "$TICKET3" --step plan --phase check \
  --attempt plan-preexisting --boundary before_transition --request plan-preexisting-transition >/dev/null
if python3 "$CONTROL" step --dir "$TICKET3" --step plan --phase finish \
    --attempt plan-preexisting --outcome success --evidence preexisting \
    --request plan-preexisting-finish >/dev/null; then
  fail 'preexisting output without artifact receipt was accepted'
fi
python3 "$CONTROL" artifact --dir "$TICKET3" --step plan --attempt plan-preexisting \
  --path 01_plan.md --scope ticket --request plan-preexisting-artifact >/dev/null
python3 "$CONTROL" step --dir "$TICKET3" --step plan --phase finish \
  --attempt plan-preexisting --outcome success --evidence recorded \
  --request plan-preexisting-finish >/dev/null

# 省略 attempt 时，相同 request 必须稳定派生 attempt，支持超时后的精确重放。
TICKET4="$WORK/.icode_output/.icode_output_4"
mkdir -p "$TICKET4"
python3 "$CONTROL" create --dir "$TICKET4" --ticket-id execution-derived-attempt \
  --requirement 'derived attempt fixture' --birth plan --request create-4 >/dev/null
AUTO_STEP=$(python3 "$CONTROL" step --dir "$TICKET4" --step plan --phase start \
  --request auto-step-start)
AUTO_STEP_ATTEMPT=$(printf '%s' "$AUTO_STEP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["attempt"])')
python3 "$CONTROL" step --dir "$TICKET4" --step plan --phase start \
  --request auto-step-start \
  | json_assert 'data["already_applied"] is True'
python3 "$CONTROL" step --dir "$TICKET4" --step plan --phase finish \
  --attempt "$AUTO_STEP_ATTEMPT" --outcome blocked --evidence fixture \
  --request auto-step-finish >/dev/null

AUTO_OPERATION=$(python3 "$CONTROL" operation --dir "$TICKET4" --phase start \
  --name inspect-only --opclass read_only --input source --request auto-operation-start)
AUTO_OPERATION_ATTEMPT=$(printf '%s' "$AUTO_OPERATION" | python3 -c 'import json,sys; print(json.load(sys.stdin)["attempt"])')
python3 "$CONTROL" operation --dir "$TICKET4" --phase start \
  --name inspect-only --opclass read_only --input source --request auto-operation-start \
  | json_assert 'data["already_applied"] is True'
python3 "$CONTROL" operation --dir "$TICKET4" --phase finish \
  --attempt "$AUTO_OPERATION_ATTEMPT" --outcome success --evidence fixture \
  --check inspected --request auto-operation-finish >/dev/null

python3 "$CONTROL" validate --dir "$TICKET" --skip-linters \
  | json_assert 'data["ok"] is True'
python3 "$CONTROL" validate --dir "$TICKET2" --skip-linters \
  | json_assert 'data["ok"] is True'
python3 "$CONTROL" validate --dir "$TICKET3" --skip-linters \
  | json_assert 'data["ok"] is True'
python3 "$CONTROL" validate --dir "$TICKET4" --skip-linters \
  | json_assert 'data["ok"] is True'

printf 'PASS: execution model contract\n'
