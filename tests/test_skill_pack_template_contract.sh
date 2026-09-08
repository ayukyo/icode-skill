#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VALIDATE="$ROOT/tools/validate_skill_pack.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0

ok() {
  printf '  PASS %s\n' "$1"
  PASS=$((PASS + 1))
}

bad() {
  printf '  FAIL %s\n' "$1" >&2
  FAIL=$((FAIL + 1))
}

PACKS="$TMP/packs"
TARGET_ROOT="$TMP/target-skills"
mkdir -p "$PACKS/fixture-skill" "$TARGET_ROOT/fixture-skill"

cat >"$PACKS/manifest.json" <<'JSON'
{
  "schema_version": 1,
  "skills": [
    {
      "name": "fixture-skill",
      "path": "fixture-skill",
      "entrypoint": "SKILL.md.template"
    }
  ]
}
JSON

cat >"$PACKS/fixture-skill/SKILL.md.template" <<'MARKDOWN'
---
name: fixture-skill
description: Use when a template publishing contract needs a fixture.
---

# Fixture skill
MARKDOWN

if "$VALIDATE" --manifest "$PACKS/manifest.json" >/dev/null 2>&1; then
  ok "validator accepts the template entrypoint contract"
else
  bad "validator accepts the template entrypoint contract"
fi

EXPECTED_LIST=$'fixture-skill\t'"$(cd "$PACKS/fixture-skill" && pwd -P)"$'\tSKILL.md.template'
if ACTUAL_LIST="$($VALIDATE --manifest "$PACKS/manifest.json" --list 2>/dev/null)" \
  && [[ "$ACTUAL_LIST" == "$EXPECTED_LIST" ]]; then
  ok "list exposes name, absolute source, and entrypoint"
else
  bad "list exposes name, absolute source, and entrypoint"
fi

cp "$PACKS/fixture-skill/SKILL.md.template" "$TARGET_ROOT/fixture-skill/SKILL.md"
if "$VALIDATE" --manifest "$PACKS/manifest.json" \
     --target-root "$TARGET_ROOT" >/dev/null 2>&1; then
  ok "installed SKILL.md matches the normalized source view"
else
  bad "installed SKILL.md matches the normalized source view"
fi

rm "$TARGET_ROOT/fixture-skill/SKILL.md"
cp "$PACKS/fixture-skill/SKILL.md.template" \
  "$TARGET_ROOT/fixture-skill/SKILL.md.template"
if "$VALIDATE" --manifest "$PACKS/manifest.json" >/dev/null 2>&1 \
  && "$VALIDATE" --manifest "$PACKS/manifest.json" \
       --target-root "$TARGET_ROOT" >/dev/null 2>&1; then
  bad "target containing only the source template is rejected"
else
  ok "target containing only the source template is rejected"
fi

cat >"$PACKS/legacy-manifest.json" <<'JSON'
{
  "schema_version": 1,
  "skills": [
    {"name": "fixture-skill", "path": "fixture-skill"}
  ]
}
JSON
if "$VALIDATE" --manifest "$PACKS/legacy-manifest.json" >/dev/null 2>&1; then
  bad "legacy manifest without entrypoint is rejected"
else
  ok "legacy manifest without entrypoint is rejected"
fi

cp "$PACKS/fixture-skill/SKILL.md.template" "$PACKS/fixture-skill/SKILL.md"
if "$VALIDATE" --manifest "$PACKS/manifest.json" >/dev/null 2>&1; then
  bad "discoverable SKILL.md files are forbidden inside source packs"
else
  ok "discoverable SKILL.md files are forbidden inside source packs"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
