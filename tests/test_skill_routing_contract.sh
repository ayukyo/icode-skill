#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VALIDATE="$ROOT/tools/validate_skill_pack.py"
MANIFEST="$ROOT/skill-packs/manifest.json"
ROUTES="$ROOT/mcp/workflow-gate/skill-routes.json"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

[[ -f "$ROUTES" ]] || fail "skill-routes.json missing"
[[ -f "$ROOT/references/skill_routing.md" ]] || fail "skill_routing.md missing"
[[ -f "$ROOT/references/host_adapters.md" ]] || fail "host_adapters.md missing"

"$VALIDATE" --manifest "$MANIFEST" --routes "$ROUTES" >/dev/null \
  || fail "repository manifest/routes validation failed"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/packs/example-skill"
cat >"$TMP/packs/example-skill/SKILL.md" <<'MARKDOWN'
---
name: example-skill
description: Use when a routing validator fixture is needed.
---
# Example
MARKDOWN
cat >"$TMP/packs/manifest.json" <<'JSON'
{"schema_version":1,"skills":[{"name":"example-skill","path":"example-skill"}]}
JSON
cat >"$TMP/routes.json" <<'JSON'
{
  "schema_version": 1,
  "routes": [{
    "skill": "missing-skill",
    "triggers": ["missing evidence"],
    "steps": ["log"],
    "input_contract": ["source"],
    "output_contract": ["verdict"],
    "fallback": "record an honest downgrade"
  }]
}
JSON
if "$VALIDATE" --manifest "$TMP/packs/manifest.json" \
     --routes "$TMP/routes.json" >/dev/null 2>&1; then
  fail "route validator accepted a missing skill"
fi

for step in log.md 01_plan.md 04_code.md 05_deepcheck.md 06_audit.md verify.md; do
  rg -q "skill_routing\.md" "$ROOT/steps/$step" \
    || fail "steps/$step does not route shared skills"
done
rg -q "references/skill_routing\.md" "$ROOT/SKILL.md" \
  || fail "SKILL.md does not expose the lazy skill router"

while IFS= read -r -d '' skill_file; do
  if rg -n "mcp__|TaskOutput|run_in_background|functions\.exec|collaboration\." \
      "$skill_file"; then
    fail "shared skill body contains host-specific tool syntax"
  fi
done < <(find "$ROOT/skill-packs" -mindepth 2 -name SKILL.md -print0)

printf 'PASS: skill routing contract\n'
