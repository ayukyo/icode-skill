#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOL="$ROOT/tools/debug_catalog.py"
TMP="$(mktemp -d -t icode-debug-catalog.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

PROJECT="$TMP/project"
DEBUG_ROOT="$PROJECT/.icode_output/.debug"
mkdir -p "$DEBUG_ROOT/twin-42" "$DEBUG_ROOT/twin-43" \
  "$PROJECT/.icode_output/.icode_output_99"

python3 - "$DEBUG_ROOT" "$PROJECT" <<'PY'
import json
import sys
from pathlib import Path

debug_root = Path(sys.argv[1])
project = Path(sys.argv[2])
manifest = {
    "schema_version": 1,
    "source_identity": {"pid": "p1", "lib": "BUG", "num": "42"},
    "activities": [{"stable_id": "a1"}],
    "artifacts": [
        {"logical_id": "f1", "sha256": "abc"},
        {"semantic_id": "remote_id:pending", "sha256": None,
         "content_hash_pending": True, "size": 10},
    ],
    "delta": {"new_semantic_evidence": False},
}
(debug_root / "twin-42" / "evidence_manifest.json").write_text(
    json.dumps(manifest, sort_keys=True), encoding="utf-8"
)
same = dict(manifest)
same["generated_at"] = "2099-01-01T00:00:00Z"
same["input_scope"] = {"previous": "volatile/location.json"}
same["delta"] = {"new_semantic_evidence": True, "new_representation_ids": ["volatile"]}
(project / "same.json").write_text(json.dumps(same, indent=4), encoding="utf-8")
changed = dict(manifest)
changed["artifacts"] = manifest["artifacts"] + [{"logical_id": "f2", "sha256": "def"}]
(project / "changed.json").write_text(json.dumps(changed, sort_keys=True), encoding="utf-8")
size_changed = json.loads(json.dumps(manifest))
size_changed["artifacts"][1]["size"] = 11
(project / "size-changed.json").write_text(json.dumps(size_changed), encoding="utf-8")

metadata_42 = {
    "ticket_id": "debug-42",
    "debug": True,
    "status": "debug_done",
    "tb_source": {"pid": "p1", "lib": "BUG", "num": "42"},
    "updated_at": "2026-09-08T09:00:00+08:00",
    "code_modules": ["broker", "nav"],
    "error_codes": ["E42"],
    "log_tags": ["shared-tag"],
    "root_cause": "bounded verdict",
    "comments": [{"content": {"comment": "raw secret comment"}}],
}
metadata_43 = {
    "ticket_id": "debug-43",
    "debug": True,
    "status": "debug_in_progress",
    "tb_source": {"pid": "p1", "lib": "BUG", "num": "43"},
    "updated_at": "2026-09-08T09:30:00+08:00",
    "code_modules": ["broker"],
    "log_tags": ["shared-tag"],
}
normal = {
    "ticket_id": "normal-99",
    "debug": False,
    "tb_source": {"pid": "p1", "lib": "BUG", "num": "99"},
}
for path, value in (
    (debug_root / "twin-42" / ".ico_metadata.json", metadata_42),
    (debug_root / "twin-43" / ".ico_metadata.json", metadata_43),
    (project / ".icode_output" / ".icode_output_99" / ".ico_metadata.json", normal),
):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
PY

printf '{broken' > "$DEBUG_ROOT/debug_catalog.json"
python3 "$TOOL" rebuild --project "$PROJECT" > "$TMP/rebuild.json"

CATALOG="$DEBUG_ROOT/debug_catalog.json"
python3 "$TOOL" query --project "$PROJECT" \
  --pid p1 --lib BUG --num 42 --manifest "$PROJECT/same.json" > "$TMP/same-query.json"
python3 "$TOOL" query --project "$PROJECT" \
  --pid p1 --lib BUG --num 42 --manifest "$PROJECT/changed.json" > "$TMP/changed-query.json"
python3 "$TOOL" query --project "$PROJECT" \
  --pid p1 --lib BUG --num 42 --manifest "$PROJECT/size-changed.json" \
  > "$TMP/size-changed-query.json"
python3 "$TOOL" query --project "$PROJECT" \
  --pid p1 --lib BUG --num 999 --keyword shared-tag > "$TMP/similar-query.json"

cp "$PROJECT/same.json" "$TMP/outside-manifest.json"
set +e
python3 "$TOOL" query --project "$PROJECT" \
  --pid p1 --lib BUG --num 42 --manifest "$TMP/outside-manifest.json" \
  >"$TMP/outside-query.log" 2>&1
OUTSIDE_RC=$?
set -e
test "$OUTSIDE_RC" -ne 0
grep -q "project" "$TMP/outside-query.log"

python3 - "$CATALOG" "$TMP/same-query.json" "$TMP/changed-query.json" \
  "$TMP/size-changed-query.json" "$TMP/similar-query.json" <<'PY'
import json
import sys
from pathlib import Path

catalog = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
same = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
changed = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
size_changed = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
similar = json.loads(Path(sys.argv[5]).read_text(encoding="utf-8"))
assert catalog["schema_version"] == 1 and catalog["scope"] == "project_debug_only"
assert len(catalog["tickets"]) == 2
assert all(item["identity"]["num"] != "99" for item in catalog["tickets"])
assert "raw secret comment" not in Path(sys.argv[1]).read_text(encoding="utf-8")
assert same["match"] == "exact" and same["action"] == "no_reanalysis"
assert changed["match"] == "exact" and changed["action"] == "incremental_analysis"
assert size_changed["action"] == "incremental_analysis"
assert similar["match"] == "similar_candidates"
assert similar["action"] == "revalidate_before_reuse"
assert {item["identity"]["num"] for item in similar["candidates"]} == {"42", "43"}
PY

test ! -e "$PROJECT/.icode_output/index.json"
test -f "$DEBUG_ROOT/twin-42/.ico_metadata.json"
test -f "$DEBUG_ROOT/twin-43/.ico_metadata.json"

ESCAPE_PROJECT="$TMP/escape-project"
ESCAPE_TARGET="$TMP/escape-target"
mkdir -p "$ESCAPE_PROJECT/.icode_output" "$ESCAPE_TARGET"
ln -s "$ESCAPE_TARGET" "$ESCAPE_PROJECT/.icode_output/.debug"
set +e
python3 "$TOOL" rebuild --project "$ESCAPE_PROJECT" \
  >"$TMP/escape-rebuild.log" 2>&1
ESCAPE_RC=$?
set -e
test "$ESCAPE_RC" -ne 0
test ! -e "$ESCAPE_TARGET/debug_catalog.json"

OUTSIDE_META="$TMP/outside-metadata.json"
printf '%s\n' '{"debug":true,"tb_source":{"pid":"private","lib":"BUG","num":"1"}}' \
  > "$OUTSIDE_META"
mkdir -p "$DEBUG_ROOT/twin-symlink"
ln -s "$OUTSIDE_META" "$DEBUG_ROOT/twin-symlink/.ico_metadata.json"
python3 "$TOOL" rebuild --project "$PROJECT" > "$TMP/rebuild-symlink.json"
if rg -q 'private' "$CATALOG"; then
  printf 'outside metadata symlink leaked into catalog\n' >&2
  exit 1
fi

printf 'PASS: debug catalog contract\n'
