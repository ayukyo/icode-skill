#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOL="$ROOT/tools/runtime_baseline.py"
TMP="$(mktemp -d -t icode-runtime-baseline.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

init_repo() {
  local path="$1" value="$2"
  git init -q -b main "$path"
  git -C "$path" config user.email fixture@example.com
  git -C "$path" config user.name fixture
  printf '%s\n' "$value" > "$path/value.txt"
  git -C "$path" add value.txt
  git -C "$path" commit -qm initial
}

init_repo "$TMP/super" super
init_repo "$TMP/child" child-v1
CHILD_RUNTIME="$(git -C "$TMP/child" rev-parse HEAD)"
printf 'child-v2\n' > "$TMP/child/value.txt"
git -C "$TMP/child" add value.txt
git -C "$TMP/child" commit -qm child-v2
CHILD_HEAD="$(git -C "$TMP/child" rev-parse HEAD)"

git -C "$TMP/super" show-ref > "$TMP/super.refs.before"
git -C "$TMP/child" show-ref > "$TMP/child.refs.before"

python3 "$TOOL" --root "$TMP" \
  --repo-map super="$TMP/super" --repo-map child="$TMP/child" \
  --candidate child="$CHILD_RUNTIME" --candidate super=deadbee \
  --verification child="$CHILD_HEAD" \
  --output "$TMP/baseline.json" --markdown "$TMP/baseline.md"

python3 - "$TMP/baseline.json" "$TMP/baseline.md" "$CHILD_RUNTIME" "$CHILD_HEAD" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
markdown = Path(sys.argv[2]).read_text(encoding="utf-8")
rows = {row["module"]: row for row in report["modules"]}
assert report["schema_version"] == 1 and report["read_only"] is True
assert report["git_side_effects"] == []
assert report["status"] == "partial"
assert set(report["input_scope"]["repo_map"]) == {"child", "super"}
assert report["input_scope"]["candidates"]["child"] == sys.argv[3]
assert report["input_scope"]["verification"]["child"] == sys.argv[4]
assert rows["child"]["runtime"]["resolved"] is True
assert rows["child"]["runtime"]["commit"] == sys.argv[3]
assert rows["child"]["analysis"]["commit"] == sys.argv[4]
assert rows["child"]["verification"]["commit"] == sys.argv[4]
assert rows["child"]["relation"] == "ancestor_of_head"
assert rows["super"]["runtime"]["resolved"] is False
assert rows["super"]["runtime"]["commit"] is None
assert "child" in markdown and "ancestor_of_head" in markdown
PY

git -C "$TMP/super" show-ref > "$TMP/super.refs.after"
git -C "$TMP/child" show-ref > "$TMP/child.refs.after"
cmp "$TMP/super.refs.before" "$TMP/super.refs.after"
cmp "$TMP/child.refs.before" "$TMP/child.refs.after"

set +e
python3 "$TOOL" --root "$TMP" --repo-map escaped=/tmp --output "$TMP/bad.json" > "$TMP/bad.log" 2>&1
BAD_RC=$?
set -e
test "$BAD_RC" -ne 0
grep -q "root" "$TMP/bad.log"

printf 'runtime log child commit %s\n' "$CHILD_RUNTIME" > "$TMP/runtime.log"
LOG_BEFORE="$(sha256sum "$TMP/runtime.log" | cut -d' ' -f1)"
set +e
python3 "$TOOL" --root "$TMP" --repo-map child="$TMP/child" \
  --log "$TMP/runtime.log" --output "$TMP/runtime.log" \
  > "$TMP/overwrite.log" 2>&1
OVERWRITE_RC=$?
set -e
test "$OVERWRITE_RC" -ne 0
test "$LOG_BEFORE" = "$(sha256sum "$TMP/runtime.log" | cut -d' ' -f1)"

set +e
OUTSIDE_MARKDOWN="${TMP}-outside.md"
python3 "$TOOL" --root "$TMP" --repo-map child="$TMP/child" \
  --candidate child="$CHILD_RUNTIME" --output "$TMP/not-created.json" \
  --markdown "$OUTSIDE_MARKDOWN" > "$TMP/markdown.log" 2>&1
MARKDOWN_RC=$?
set -e
test "$MARKDOWN_RC" -ne 0
test ! -e "$TMP/not-created.json"
test ! -e "$OUTSIDE_MARKDOWN"

HEAD_BEFORE="$(sha256sum "$TMP/child/.git/HEAD" | cut -d' ' -f1)"
set +e
python3 "$TOOL" --root "$TMP" --repo-map child="$TMP/child" \
  --candidate child="$CHILD_RUNTIME" --output "$TMP/child/.git/HEAD" \
  > "$TMP/git-control.log" 2>&1
GIT_CONTROL_RC=$?
set -e
test "$GIT_CONTROL_RC" -ne 0
test "$HEAD_BEFORE" = "$(sha256sum "$TMP/child/.git/HEAD" | cut -d' ' -f1)"

set +e
python3 "$TOOL" --root "$TMP" --repo-map child="$TMP/child" \
  --candidate typo="$CHILD_RUNTIME" --output "$TMP/unknown-module.json" \
  > "$TMP/unknown-module.log" 2>&1
UNKNOWN_RC=$?
set -e
test "$UNKNOWN_RC" -ne 0
test ! -e "$TMP/unknown-module.json"
grep -q "module" "$TMP/unknown-module.log"

printf 'PASS: runtime baseline resolver contract\n'
