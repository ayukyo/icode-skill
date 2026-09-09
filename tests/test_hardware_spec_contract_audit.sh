#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

NAME="hardware-spec-contract-audit"
SKILL="skill-packs/$NAME/SKILL.md.template"
MANIFEST="skill-packs/manifest.json"
ROUTES="mcp/workflow-gate/skill-routes.json"

if python3 - "$MANIFEST" "$ROUTES" "$NAME" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
routes = json.load(open(sys.argv[2], encoding="utf-8"))
name = sys.argv[3]
assert name in {item["name"] for item in manifest["skills"]}
route = next(item for item in routes["routes"] if item["skill"] == name)
assert {"plan", "code", "deepcheck", "audit", "verify"} <= set(route["steps"])
assert {"source_register", "normalized_fact_matrix", "conflict_register", "traceability_matrix", "verdict"} <= set(route["output_contract"])
assert route["triggers"] and route["input_contract"] and route["fallback"]
PY
then ok "规格契约技能已发布并接入既有步骤"; else bad "规格契约 manifest/routes 合同缺失"; fi

TOKENS=(
  'source_register'
  'normalized_fact_matrix'
  'conflict_register'
  'traceability_matrix'
  'verdict'
  'requirement'
  'external_spec'
  'implementation'
  'measured'
  'product/variant'
  'revision'
  'lifecycle_status'
  'true_contradiction'
  'scope_or_variant_difference'
  'lifecycle_drift'
  'unit_or_rounding'
  'unresolved'
  'field-scoped authority'
  'technical-document-intake'
  'schematic-interface-audit'
)
CONTENT_OK=1
if [[ ! -f "$SKILL" ]]; then
  CONTENT_OK=0
else
  for token in "${TOKENS[@]}"; do
    rg -qF "$token" "$SKILL" || CONTENT_OK=0
  done
fi
if [[ "$CONTENT_OK" -eq 1 ]] \
  && head -4 "$SKILL" | rg -q '^description: Use when' \
  && rg -qi 'no global authority|never.*global authority|禁止.*全局权威' "$SKILL" \
  && ! rg -q 'mcp__|TaskOutput|functions\.exec|collaboration\.' "$SKILL"; then
  ok "技能覆盖字段级权威、状态/变体和冲突分类"
else
  bad "技能正文缺规格契约关键边界"
fi

if [[ -f "$SKILL" ]] \
  && rg -q 'Requirement.*Published spec.*Schematic.*Code.*Test.*Field|requirement.*external spec.*schematic.*code.*test.*field' "$SKILL" \
  && rg -q 'raw value.*unit.*normalized|Raw value.*Unit.*Normalized' "$SKILL" \
  && rg -q 'page.*section.*table.*cell|Page.*Section.*Table.*Cell' "$SKILL"; then
  ok "事实位置、单位归一化和端到端追溯可复核"
else
  bad "规格事实定位/归一化/追溯合同不完整"
fi

if [[ -f "tests/skill-evals/$NAME.json" ]] \
  && [[ -f "tests/skill-evals/results/$NAME-red.md" ]] \
  && [[ -f "tests/skill-evals/results/$NAME-green.md" ]] \
  && python3 - "tests/skill-evals/$NAME.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["skill"] == "hardware-spec-contract-audit"
assert len(d["scenarios"]) >= 4
assert set(d["required_output"]) == {
    "source_register", "normalized_fact_matrix", "conflict_register",
    "traceability_matrix", "verdict"
}
PY
then ok "规格契约技能保留四场景 RED/GREEN eval"; else bad "规格契约 eval 证据缺失"; fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
