#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LEARN="$ROOT/tools/learn.py"
MANIFEST="$ROOT/skill-packs/manifest.json"
ROUTES="$ROOT/mcp/workflow-gate/skill-routes.json"
TMP="$(mktemp -d -t icode-learn.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

PROJECT="$TMP/project"
REPORT="$PROJECT/.icode_output/learn/test-report"
mkdir -p "$PROJECT/.icode_output"

python3 - "$PROJECT" <<'PY'
import json
import sys
from pathlib import Path

project = Path(sys.argv[1])
groups = [
    ("field_log_analysis", "unrouted", False, "degraded", 3),
    ("cross_node_incident+deployment_identity_claim", "unrouted", False, "degraded", 3),
    ("verified_or_completed_claim", "field-verification-boundary", True, "degraded", 3),
    ("binary-symbol-provenance-gap", "unrouted", False, "degraded", 3),
    ("duplicate attachment schema validator", "unrouted", False, "degraded", 3),
    ("one-off-project-layout", "unrouted", False, "degraded", 2),
]
ticket_no = 0
for trigger, skill, adopted, result, count in groups:
    for occurrence in range(count):
        ticket_no += 1
        out = project / ".icode_output" / f".icode_output_{ticket_no}"
        out.mkdir()
        run = {
            "run_id": f"run-{ticket_no}",
            "at": f"2026-09-{ticket_no:02d}T10:00:00+08:00",
            "skill": skill,
            "trigger": trigger,
            "result": result,
            "adopted": adopted,
            "evidence_refs": [f"ticket:{ticket_no}:evidence"],
            "elapsed_ms": 1000 + occurrence,
            "estimated_tokens": 700 + occurrence,
            "unique_findings": [f"finding-{trigger}-{occurrence}"],
            "agent_id": "fixture",
        }
        metadata = {
            "schema_version": 3,
            "ticket_id": f"ticket-{ticket_no}",
            "created_at": f"2026-09-{ticket_no:02d}T09:00:00+08:00",
            "extensions": {"skills": {"runs": [run]}},
            "claims": [{"claim": f"bounded-{trigger}", "evidence_refs": run["evidence_refs"]}],
        }
        (out / ".ico_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )

repeat = project / ".icode_output" / ".icode_output_repeat"
repeat.mkdir()
repeat_runs = []
for occurrence in range(3):
    repeat_runs.append({
        "run_id": f"repeat-{occurrence}",
        "at": f"2026-09-0{occurrence + 1}T12:00:00+08:00",
        "skill": "unrouted",
        "trigger": "same-ticket-repeat",
        "result": "degraded",
        "adopted": False,
        "evidence_refs": [f"same-ticket:{occurrence}"],
    })
(repeat / ".ico_metadata.json").write_text(json.dumps({
    "schema_version": 3,
    "ticket_id": "one-ticket",
    "extensions": {"skills": {"runs": repeat_runs}},
}), encoding="utf-8")

broken = project / ".icode_output" / ".icode_output_broken"
broken.mkdir()
(broken / ".ico_metadata.json").write_text("{broken", encoding="utf-8")
wrong_type = project / ".icode_output" / ".icode_output_wrong_type"
wrong_type.mkdir()
(wrong_type / ".ico_metadata.json").write_text("[]", encoding="utf-8")
wrong_extensions = project / ".icode_output" / ".icode_output_wrong_extensions"
wrong_extensions.mkdir()
(wrong_extensions / ".ico_metadata.json").write_text(
    json.dumps({"ticket_id": "bad-extensions", "extensions": "invalid"}),
    encoding="utf-8",
)
PY

mkdir -p "$TMP/outside" "$PROJECT/.icode_output/.icode_output_symlink"
python3 - "$TMP/outside/.ico_metadata.json" <<'PY'
import json
import sys
from pathlib import Path

runs = [{
    "run_id": f"outside-{index}",
    "at": f"2026-09-0{index + 1}T12:00:00+08:00",
    "skill": "unrouted",
    "trigger": "outside-private-trigger",
    "result": "degraded",
    "adopted": False,
} for index in range(3)]
Path(sys.argv[1]).write_text(json.dumps({
    "ticket_id": "outside-private",
    "extensions": {"skills": {"runs": runs}},
}), encoding="utf-8")
PY
ln -s "$TMP/outside/.ico_metadata.json" \
  "$PROJECT/.icode_output/.icode_output_symlink/.ico_metadata.json"

MANIFEST_BEFORE="$(sha256sum "$MANIFEST" | cut -d' ' -f1)"
ROUTES_BEFORE="$(sha256sum "$ROUTES" | cut -d' ' -f1)"

python3 "$LEARN" --project "$PROJECT" --output-dir "$REPORT"

python3 - "$REPORT/learning_report.json" "$REPORT/learning_report.md" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
markdown = Path(sys.argv[2]).read_text(encoding="utf-8")
assert report["schema_version"] == 1
assert report["source_scope"]["project"]
assert report["read_only_sources"] is True
assert report["manifest_modified"] is False
assert report["routes_modified"] is False
assert report["status"] == "partial"
assert any("broken" in item["path"] for item in report["parse_failures"])
by_trigger = {item["trigger"]: item for item in report["candidates"]}
assert by_trigger["field_log_analysis"]["classification"] == "reuse_existing_skill"
assert by_trigger["cross_node_incident+deployment_identity_claim"]["classification"] == "compose_existing_skills"
assert by_trigger["verified_or_completed_claim"]["classification"] == "improve_existing_skill"
assert by_trigger["binary-symbol-provenance-gap"]["classification"] == "create_new_skill"
assert by_trigger["duplicate attachment schema validator"]["classification"] == "automate_in_tooling"
assert by_trigger["one-off-project-layout"]["classification"] == "no_action"
assert by_trigger["same-ticket-repeat"]["occurrences"] == 1
assert by_trigger["same-ticket-repeat"]["classification"] == "no_action"
assert "outside-private-trigger" not in by_trigger
assert all(
    item["occurrences"] >= 3
    for item in report["candidates"]
    if item["classification"] == "create_new_skill"
)
assert "人工批准" in markdown and "RED" in markdown and "GREEN" in markdown
notes = sorted(Path(sys.argv[1]).parent.glob("skill_candidate_*.md"))
assert notes and all("不是 SKILL.md" in path.read_text(encoding="utf-8") for path in notes)
PY

test "$MANIFEST_BEFORE" = "$(sha256sum "$MANIFEST" | cut -d' ' -f1)"
test "$ROUTES_BEFORE" = "$(sha256sum "$ROUTES" | cut -d' ' -f1)"
test ! -e "$PROJECT/.icode_output/index.json"

set +e
python3 "$LEARN" --project "$PROJECT" --since not-a-date --output-dir "$TMP/bad" >"$TMP/bad.log" 2>&1
BAD_RC=$?
set -e
test "$BAD_RC" -ne 0
grep -q "ISO" "$TMP/bad.log"

set +e
python3 "$LEARN" --project "$PROJECT" --output-dir "$TMP/outside-report" \
  >"$TMP/outside-output.log" 2>&1
OUTSIDE_RC=$?
set -e
test "$OUTSIDE_RC" -ne 0
test ! -e "$TMP/outside-report"
grep -q "project" "$TMP/outside-output.log"

set +e
python3 "$LEARN" --project "$PROJECT" --output-dir "$PROJECT/.git" \
  >"$TMP/git-output.log" 2>&1
GIT_OUTPUT_RC=$?
set -e
test "$GIT_OUTPUT_RC" -ne 0
test ! -e "$PROJECT/.git"

python3 - "$ROOT/tools" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from learn import _slug

assert _slug("name+a") != _slug("name a")
PY

for classification in reuse_existing_skill compose_existing_skills \
  improve_existing_skill create_new_skill automate_in_tooling no_action; do
  grep -q "$classification" "$ROOT/steps/learn.md"
done
grep -q "至少 3 个独立实例" "$ROOT/steps/learn.md"
grep -q "RED" "$ROOT/steps/learn.md"
grep -q "GREEN" "$ROOT/steps/learn.md"
grep -q "人工批准" "$ROOT/steps/learn.md"
grep -q "sync-to-global.sh.*--apply" "$ROOT/steps/learn.md"
grep -q "默认等级.*L0" "$ROOT/steps/learn.md"

printf 'PASS: learning workflow contract\n'
