#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALLER="$ROOT/tools/install_skill_pack.py"
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

tree_hash() {
  local root="$1"
  find "$root" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1
}

PACKS="$TMP/packs"
mkdir -p "$PACKS/fixture-skill"
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
description: Use when an installer contract needs a deterministic fixture.
---

# Fixture skill
MARKDOWN
printf 'fixture reference\n' >"$PACKS/fixture-skill/reference.md"

FRESH="$TMP/fresh"
if [[ -x "$INSTALLER" ]] \
  && python3 "$INSTALLER" --manifest "$PACKS/manifest.json" \
     --target-root "$FRESH" >/dev/null 2>&1 \
  && cmp -s "$PACKS/fixture-skill/SKILL.md.template" \
       "$FRESH/fixture-skill/SKILL.md" \
  && cmp -s "$PACKS/fixture-skill/reference.md" \
       "$FRESH/fixture-skill/reference.md" \
  && [[ -f "$FRESH/fixture-skill/.icode-skill-owner.json" ]] \
  && [[ ! -e "$FRESH/fixture-skill/SKILL.md.template" ]] \
  && python3 - "$FRESH/fixture-skill/.icode-skill-owner.json" <<'PY'
import json
import sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert value == {"schema_version": 1, "owner": "icode-skill", "skill": "fixture-skill"}
PY
then
  ok "fresh target installs normalized payload and ownership marker"
else
  bad "fresh target installs normalized payload and ownership marker"
fi

if [[ -d "$FRESH/fixture-skill" ]]; then
  BEFORE_REPEAT="$(tree_hash "$FRESH/fixture-skill")"
  BEFORE_REPEAT_INODE="$(stat -c %i "$FRESH/fixture-skill")"
else
  BEFORE_REPEAT="missing"
  BEFORE_REPEAT_INODE="missing"
fi
if [[ -x "$INSTALLER" ]] \
  && python3 "$INSTALLER" --manifest "$PACKS/manifest.json" \
     --target-root "$FRESH" >"$TMP/repeat-output.txt" 2>&1 \
  && [[ "$BEFORE_REPEAT" == "$(tree_hash "$FRESH/fixture-skill")" ]] \
  && [[ "$BEFORE_REPEAT_INODE" == "$(stat -c %i "$FRESH/fixture-skill")" ]] \
  && grep -q '^current:' "$TMP/repeat-output.txt"; then
  ok "repeated installation is a no-write current-state operation"
else
  bad "repeated installation is a no-write current-state operation"
fi

printf 'local runtime config\n' >"$FRESH/fixture-skill/config.json"
printf 'stale managed payload\n' >"$FRESH/fixture-skill/stale.txt"
printf 'updated fixture reference\n' >"$PACKS/fixture-skill/reference.md"
if python3 "$INSTALLER" --manifest "$PACKS/manifest.json" \
     --target-root "$FRESH" >/dev/null 2>&1 \
  && grep -qxF 'local runtime config' "$FRESH/fixture-skill/config.json" \
  && grep -qxF 'updated fixture reference' "$FRESH/fixture-skill/reference.md" \
  && [[ ! -e "$FRESH/fixture-skill/stale.txt" ]]; then
  ok "owned update preserves runtime config and removes stale payload"
else
  bad "owned update preserves runtime config and removes stale payload"
fi

ADOPT="$TMP/adopt"
mkdir -p "$ADOPT/fixture-skill"
cp "$PACKS/fixture-skill/SKILL.md.template" "$ADOPT/fixture-skill/SKILL.md"
cp "$PACKS/fixture-skill/reference.md" "$ADOPT/fixture-skill/reference.md"
if [[ -x "$INSTALLER" ]] \
  && python3 "$INSTALLER" --manifest "$PACKS/manifest.json" \
     --target-root "$ADOPT" >/dev/null 2>&1 \
  && [[ -f "$ADOPT/fixture-skill/.icode-skill-owner.json" ]]; then
  ok "identical unmanaged target is adopted without data loss"
else
  bad "identical unmanaged target is adopted without data loss"
fi

CONFLICT="$TMP/conflict"
mkdir -p "$CONFLICT/fixture-skill"
printf 'user-owned skill\n' >"$CONFLICT/fixture-skill/SKILL.md"
CONFLICT_BEFORE="$(tree_hash "$CONFLICT/fixture-skill")"
if [[ ! -x "$INSTALLER" ]]; then
  bad "different unmanaged target is rejected"
elif python3 "$INSTALLER" --manifest "$PACKS/manifest.json" \
       --target-root "$CONFLICT" >/dev/null 2>&1; then
  bad "different unmanaged target is rejected"
elif [[ "$CONFLICT_BEFORE" == "$(tree_hash "$CONFLICT/fixture-skill")" ]] \
  && [[ ! -e "$CONFLICT/fixture-skill/.icode-skill-owner.json" ]]; then
  ok "different unmanaged target is rejected"
else
  bad "different unmanaged target remains byte-for-byte unchanged"
fi

ATOMIC_FRESH="$TMP/atomic-fresh"
if [[ ! -x "$INSTALLER" ]]; then
  bad "multi-root conflict is detected before any target write"
elif python3 "$INSTALLER" --manifest "$PACKS/manifest.json" \
       --target-root "$ATOMIC_FRESH" --target-root "$CONFLICT" \
       >/dev/null 2>&1; then
  bad "multi-root conflict fails the transaction"
elif [[ ! -e "$ATOMIC_FRESH" ]] \
  && [[ "$CONFLICT_BEFORE" == "$(tree_hash "$CONFLICT/fixture-skill")" ]]; then
  ok "multi-root conflict is detected before any target write"
else
  bad "multi-root conflict is detected before any target write"
fi

DRY="$TMP/dry-run"
if [[ -x "$INSTALLER" ]] \
  && python3 "$INSTALLER" --manifest "$PACKS/manifest.json" \
     --target-root "$DRY" --dry-run >/dev/null 2>&1 \
  && [[ ! -e "$DRY" ]]; then
  ok "dry-run performs zero writes"
else
  bad "dry-run performs zero writes"
fi

BAD_OWNER="$TMP/bad-owner"
mkdir -p "$BAD_OWNER/fixture-skill"
cp "$PACKS/fixture-skill/SKILL.md.template" "$BAD_OWNER/fixture-skill/SKILL.md"
cp "$PACKS/fixture-skill/reference.md" "$BAD_OWNER/fixture-skill/reference.md"
cat >"$BAD_OWNER/fixture-skill/.icode-skill-owner.json" <<'JSON'
{"schema_version":1,"owner":"someone-else","skill":"fixture-skill"}
JSON
BAD_OWNER_BEFORE="$(tree_hash "$BAD_OWNER/fixture-skill")"
if [[ ! -x "$INSTALLER" ]]; then
  bad "foreign ownership marker is rejected without mutation"
elif python3 "$INSTALLER" --manifest "$PACKS/manifest.json" \
       --target-root "$BAD_OWNER" >/dev/null 2>&1; then
  bad "foreign ownership marker is rejected"
elif [[ "$BAD_OWNER_BEFORE" == "$(tree_hash "$BAD_OWNER/fixture-skill")" ]]; then
  ok "foreign ownership marker is rejected without mutation"
else
  bad "foreign ownership marker is rejected without mutation"
fi

if [[ -x "$INSTALLER" ]] \
  && [[ -d "$FRESH/fixture-skill" ]] \
  && "$VALIDATE" --manifest "$PACKS/manifest.json" \
       --target-root "$FRESH" >/dev/null 2>&1; then
  ok "installed target passes the shared validator"
else
  bad "installed target passes the shared validator"
fi

if python3 "$INSTALLER" --manifest "$PACKS/manifest.json" \
     --target-root / --dry-run >/dev/null 2>&1; then
  bad "over-broad filesystem root is rejected"
else
  ok "over-broad filesystem root is rejected"
fi

KEEP_EXTRA="$TMP/keep-extra"
if python3 "$INSTALLER" --manifest "$PACKS/manifest.json" \
     --target-root "$KEEP_EXTRA" >/dev/null 2>&1; then
  printf 'keep me\n' >"$KEEP_EXTRA/fixture-skill/user-note.txt"
fi
printf 'keep-extra source update\n' >"$PACKS/fixture-skill/reference.md"
if python3 "$INSTALLER" --manifest "$PACKS/manifest.json" \
     --target-root "$KEEP_EXTRA" --keep-extra >/dev/null 2>&1 \
  && grep -qxF 'keep me' "$KEEP_EXTRA/fixture-skill/user-note.txt" \
  && grep -qxF 'keep-extra source update' \
       "$KEEP_EXTRA/fixture-skill/reference.md"; then
  ok "keep-extra preserves target-only files while updating source payload"
else
  bad "keep-extra preserves target-only files while updating source payload"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
