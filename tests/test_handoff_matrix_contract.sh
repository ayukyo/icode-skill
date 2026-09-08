#!/usr/bin/env bash
# handoff matrix contract: multi-repo delivery facts must be read-only and evidence-bounded.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GUARD="$SCRIPT_DIR/../scripts/submission_guard.py"
TMP=$(mktemp -d -t icode_handoff_XXXXX)
trap 'rm -rf "$TMP"' EXIT
FAIL=0

new_repo() {
  local path="$1"
  git init -q -b main "$path"
  git -C "$path" config user.email test@example.com
  git -C "$path" config user.name test
  printf '%s\n' seed > "$path/seed.txt"
  git -C "$path" add seed.txt
  git -C "$path" commit -qm init
}

SUPER="$TMP/super"
SUB="$TMP/sub"
new_repo "$SUPER"
new_repo "$SUB"
printf '%s\n' changed > "$SUPER/source.cpp"

META="$TMP/.ico_metadata.json"
python3 - "$META" "$SUPER" "$SUB" <<'PY'
import json
import sys

metadata, super_repo, sub_repo = sys.argv[1:]
data = {
    "requirement": "handoff contract",
    "submission_contracts": [
        {
            "repo_role": "super",
            "repo_path": super_repo,
            "worktree_branch": "main",
            "target_remote_ref": "refs/remotes/origin/main",
        },
        {
            "repo_role": "sub",
            "repo_path": sub_repo,
            "worktree_branch": "main",
            "target_remote_ref": "refs/remotes/origin/main",
        },
    ],
    "extensions": {
        "handoff": {
            "inputs": [
                {
                    "repo_path": super_repo,
                    "modified": True,
                    "build_participant": True,
                    "deployed": True,
                    "submit_required": True,
                    "submit_reason": "modified",
                    "docs_required": True,
                    "docs_ready": False,
                    "excluded_paths": [
                        {"path": "build/", "reason": "generated build output"},
                        {"path": ".icode_output/", "reason": "workflow artifacts"},
                    ],
                    "artifact_identity": "sha256:abc123",
                }
            ]
        }
    },
}
with open(metadata, "w", encoding="utf-8") as stream:
    json.dump(data, stream, ensure_ascii=False, indent=2)
PY

JSON_OUT="$TMP/handoff.json"
MD_OUT="$TMP/handoff.md"
META_BEFORE=$(sha256sum "$META" | awk '{print $1}')
SUPER_HEAD_BEFORE=$(git -C "$SUPER" rev-parse HEAD)
SUB_HEAD_BEFORE=$(git -C "$SUB" rev-parse HEAD)
SUPER_STATUS_BEFORE=$(git -C "$SUPER" status --porcelain=v1)
SUB_STATUS_BEFORE=$(git -C "$SUB" status --porcelain=v1)

python3 "$GUARD" handoff \
  --metadata "$META" \
  --output "$JSON_OUT" \
  --markdown "$MD_OUT"

python3 - "$JSON_OUT" "$SUPER" "$SUB" <<'PY'
import json
import sys

report_path, super_repo, sub_repo = sys.argv[1:]
with open(report_path, encoding="utf-8") as stream:
    report = json.load(stream)

assert report["schema_version"] == "1.0"
assert report["read_only"] is True
assert len(report["repositories"]) == 2
rows = {row["repo_path"]: row for row in report["repositories"]}

known = rows[super_repo]
for key in (
    "modified", "build_participant", "deployed", "submit_required",
    "submit_reason", "docs_required", "docs_ready", "excluded_paths",
    "artifact_identity",
):
    assert key in known, key
assert known["modified"] is True
assert known["build_participant"] is True
assert known["deployed"] is True
assert known["submit_required"] is True
assert known["submit_reason"] == "modified"
assert known["docs_required"] is True
assert known["docs_ready"] is False
assert known["artifact_identity"] == "sha256:abc123"
assert known["dirty"] is True
assert known["excluded_paths"][0]["path"] == "build/"

unknown = rows[sub_repo]
assert unknown["dirty"] is False
assert unknown["modified"] is None
assert unknown["build_participant"] is None
assert unknown["deployed"] is None
assert unknown["submit_required"] is None
assert unknown["submit_reason"] == "unresolved"
assert unknown["docs_required"] is None
assert unknown["docs_ready"] is None
assert unknown["excluded_paths"] == []
assert unknown["artifact_identity"] is None
PY

# Facts must remain conservative when contracts or explicit handoff inputs are malformed/conflicting.
if python3 - "$GUARD" "$META" "$SUPER" <<'PY'
import importlib.util
import pathlib
import sys

script, metadata, repo = sys.argv[1:]
spec = importlib.util.spec_from_file_location("submission_guard_facts", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
metadata_path = pathlib.Path(metadata)
failures = []

def check(name, function):
    try:
        function()
        print(f"PASS: {name}")
    except Exception as error:
        print(f"FAIL: {name}: {type(error).__name__}: {error}")
        failures.append(name)

def malformed_contract_collection():
    report = module.build_handoff_report(
        {"submission_contracts": "not-a-list"}, metadata_path
    )
    assert report["repositories"] == []
    assert "invalid_submission_contracts" in report["issues"]

def malformed_contract_entries():
    report = module.build_handoff_report(
        {"submission_contracts": [None, "not-an-object"]}, metadata_path
    )
    assert len(report["repositories"]) == 2
    assert all(row["resolution"] == "unresolved" for row in report["repositories"])
    assert all("invalid_submission_contract" in row["issues"] for row in report["repositories"])

def missing_repo_paths_do_not_read_cwd():
    report = module.build_handoff_report(
        {
            "submission_contracts": [
                {"repo_role": "missing"},
                {"repo_role": "empty", "repo_path": ""},
            ]
        },
        metadata_path,
    )
    assert len(report["repositories"]) == 2
    for row in report["repositories"]:
        assert row["resolution"] == "unresolved"
        assert row["repo_path"] is None
        assert row["branch"] is None
        assert row["head"] is None
        assert row["dirty"] is None
        assert row["modified"] is None
        assert "missing_repo_path" in row["issues"]

def contradictory_submit_facts_are_unresolved():
    report = module.build_handoff_report(
        {
            "submission_contracts": [{"repo_role": "super", "repo_path": repo}],
            "extensions": {"handoff": {"inputs": [{
                "repo_path": repo,
                "modified": False,
                "submit_required": True,
                "submit_reason": "modified",
            }]}},
        },
        metadata_path,
    )
    row = report["repositories"][0]
    assert row["modified"] is False
    assert row["submit_required"] is None
    assert row["submit_reason"] == "unresolved"
    assert "submit_disposition_conflict" in row["issues"]

def unsafe_exclusions_never_filter_dirty_files():
    report = module.build_handoff_report(
        {
            "submission_contracts": [{"repo_role": "super", "repo_path": repo}],
            "extensions": {"handoff": {"inputs": [{
                "repo_path": repo,
                "excluded_paths": [
                    "../source.cpp", "/tmp/source.cpp", "C:\\temp\\source.cpp", "build/"
                ],
            }]}},
        },
        metadata_path,
    )
    row = report["repositories"][0]
    assert row["modified"] is True
    assert row["excluded_paths"] == [{"path": "build/", "reason": "explicitly excluded"}]
    assert sum(issue.startswith("invalid_excluded_path:") for issue in row["issues"]) == 3

def deployed_requires_artifact_or_deployment_evidence():
    report = module.build_handoff_report(
        {
            "submission_contracts": [{"repo_role": "super", "repo_path": repo}],
            "extensions": {"handoff": {"inputs": [{
                "repo_path": repo,
                "deployed": True,
                "deploy_target": "device-A",
            }]}},
        },
        metadata_path,
    )
    row = report["repositories"][0]
    assert row["deployed"] is None
    assert row["deploy_target"] == "device-A"
    assert row["deployment_evidence"] == []
    assert "deployed_without_evidence" in row["issues"]

check("non-list submission_contracts is reported, not raised", malformed_contract_collection)
check("non-object contracts produce unresolved rows", malformed_contract_entries)
check("missing repo_path never falls back to cwd", missing_repo_paths_do_not_read_cwd)
check("contradictory submit facts become unresolved", contradictory_submit_facts_are_unresolved)
check("unsafe exclusions cannot hide dirty files", unsafe_exclusions_never_filter_dirty_files)
check("deployed=true requires evidence", deployed_requires_artifact_or_deployment_evidence)
if failures:
    raise SystemExit(1)
PY
then
  echo "PASS: handoff facts are structurally valid and internally consistent"
else
  echo "FAIL: handoff fact consistency contract"
  FAIL=$((FAIL + 1))
fi

grep -qF '| super |' "$MD_OUT"
grep -qF '| sub |' "$MD_OUT"
grep -qF 'unresolved' "$MD_OUT"
grep -qF 'sha256:abc123' "$MD_OUT"

test "$META_BEFORE" = "$(sha256sum "$META" | awk '{print $1}')"
test "$SUPER_HEAD_BEFORE" = "$(git -C "$SUPER" rev-parse HEAD)"
test "$SUB_HEAD_BEFORE" = "$(git -C "$SUB" rev-parse HEAD)"
test "$SUPER_STATUS_BEFORE" = "$(git -C "$SUPER" status --porcelain=v1)"
test "$SUB_STATUS_BEFORE" = "$(git -C "$SUB" status --porcelain=v1)"

expect_rejected_without_metadata_change() {
  local description="$1"
  local metadata="$2"
  shift 2
  local before after rc
  before=$(sha256sum "$metadata" | awk '{print $1}')
  set +e
  python3 "$GUARD" handoff --metadata "$metadata" "$@" > "$TMP/reject.log" 2>&1
  rc=$?
  set -e
  after=$(sha256sum "$metadata" | awk '{print $1}')
  if [ "$rc" -ne 0 ] && [ "$before" = "$after" ]; then
    echo "PASS: $description"
  else
    echo "FAIL: $description (rc=$rc metadata_unchanged=$([ "$before" = "$after" ] && echo yes || echo no))"
    FAIL=$((FAIL + 1))
  fi
}

# Output destinations are untrusted CLI input. Reject all aliases before writing either report.
META_AS_JSON="$TMP/metadata-as-json.json"
cp "$META" "$META_AS_JSON"
expect_rejected_without_metadata_change \
  "--output cannot overwrite metadata" "$META_AS_JSON" \
  --output "$META_AS_JSON"

META_AS_MD="$TMP/metadata-as-markdown.json"
cp "$META" "$META_AS_MD"
SAFE_JSON_FOR_BAD_MD="$TMP/not-created.json"
expect_rejected_without_metadata_change \
  "--markdown cannot overwrite metadata" "$META_AS_MD" \
  --output "$SAFE_JSON_FOR_BAD_MD" --markdown "$META_AS_MD"
if [ -e "$SAFE_JSON_FOR_BAD_MD" ]; then
  echo "FAIL: destination validation must happen before JSON output"
  FAIL=$((FAIL + 1))
fi

META_SAME_TARGET="$TMP/metadata-same-target.json"
cp "$META" "$META_SAME_TARGET"
SAME_TARGET="$TMP/same-target.out"
expect_rejected_without_metadata_change \
  "--output and --markdown cannot resolve to the same target" "$META_SAME_TARGET" \
  --output "$SAME_TARGET" --markdown "$SAME_TARGET"

TICKET_SCOPE="$TMP/ticket-scope"
mkdir -p "$TICKET_SCOPE"
META_SCOPED="$TICKET_SCOPE/.ico_metadata.json"
cp "$META" "$META_SCOPED"
expect_rejected_without_metadata_change \
  "handoff reports stay inside the ticket directory" "$META_SCOPED" \
  --output "$TMP/outside-ticket.json"
test ! -e "$TMP/outside-ticket.json" || FAIL=$((FAIL + 1))

META_ICO_JSON="$TMP/metadata-ico-json.json"
cp "$META" "$META_ICO_JSON"
ICO_JSON="$TMP/.ico_events.jsonl"
printf '%s\n' control-sentinel > "$ICO_JSON"
ICO_JSON_BEFORE=$(sha256sum "$ICO_JSON" | awk '{print $1}')
expect_rejected_without_metadata_change \
  "--output cannot overwrite .ico_* control files" "$META_ICO_JSON" \
  --output "$ICO_JSON"
test "$ICO_JSON_BEFORE" = "$(sha256sum "$ICO_JSON" | awk '{print $1}')" || FAIL=$((FAIL + 1))

META_ICO_MD="$TMP/metadata-ico-md.json"
cp "$META" "$META_ICO_MD"
ICO_MD="$TMP/.ico_decision_anchors.json"
printf '%s\n' control-sentinel > "$ICO_MD"
ICO_MD_BEFORE=$(sha256sum "$ICO_MD" | awk '{print $1}')
SAFE_JSON_FOR_ICO_MD="$TMP/not-created-for-ico-md.json"
expect_rejected_without_metadata_change \
  "--markdown cannot overwrite .ico_* control files" "$META_ICO_MD" \
  --output "$SAFE_JSON_FOR_ICO_MD" --markdown "$ICO_MD"
test "$ICO_MD_BEFORE" = "$(sha256sum "$ICO_MD" | awk '{print $1}')" || FAIL=$((FAIL + 1))
test ! -e "$SAFE_JSON_FOR_ICO_MD" || FAIL=$((FAIL + 1))

# Atomic Markdown writes must leave an existing destination intact if the final replace fails.
ATOMIC_MD="$TMP/atomic.md"
printf '%s\n' original-markdown > "$ATOMIC_MD"
if python3 - "$GUARD" "$ATOMIC_MD" <<'PY'
import importlib.util
import pathlib
import sys

script, destination = sys.argv[1:]
spec = importlib.util.spec_from_file_location("submission_guard", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def fail_replace(source, target):
    raise OSError("simulated replace failure")

module.os.replace = fail_replace
try:
    module.atomically_write_text(pathlib.Path(destination), "replacement\n")
except OSError:
    pass
else:
    raise AssertionError("simulated replace failure was not propagated")
assert pathlib.Path(destination).read_text(encoding="utf-8") == "original-markdown\n"
PY
then
  echo "PASS: markdown write is atomic"
else
  echo "FAIL: markdown write is not atomic"
  FAIL=$((FAIL + 1))
fi

if [ "$FAIL" -ne 0 ]; then
  echo "FAIL: $FAIL handoff safety assertions failed"
  exit 1
fi

echo "PASS: handoff matrix is evidence-bounded, path-safe, atomic, and read-only"
