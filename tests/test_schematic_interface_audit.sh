#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

NAME="schematic-interface-audit"
SKILL="skill-packs/$NAME/SKILL.md.template"
MANIFEST="skill-packs/manifest.json"
ROUTES="mcp/workflow-gate/skill-routes.json"

if python3 - "$MANIFEST" "$ROUTES" "$NAME" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
routes = json.load(open(sys.argv[2], encoding="utf-8"))
name = sys.argv[3]
assert len(manifest["skills"]) == 16
assert name in {item["name"] for item in manifest["skills"]}
route = next(item for item in routes["routes"] if item["skill"] == name)
assert {"log", "plan", "code", "deepcheck", "audit", "verify"} <= set(route["steps"])
assert {"schematic_scope", "source_quality", "sheet_map", "interface_matrix", "power_sequence", "ambiguities", "verdict"} <= set(route["output_contract"])
assert route["triggers"] and route["input_contract"] and route["fallback"]
PY
then ok "原理图技能已作为共享技能发布并路由"; else bad "原理图 manifest/routes 合同缺失"; fi

TOKENS=(
  'schematic_scope'
  'source_quality'
  'sheet_map'
  'interface_matrix'
  'power_sequence'
  'ambiguities'
  'verdict'
  'direct_eda_or_netlist'
  'vector_searchable_pdf'
  'raster_or_ocr'
  'cross-sheet'
  'connector'
  'direction'
  'voltage domain'
  'pull-up/pull-down'
  'termination'
  'protection'
  'level shifting'
  'clock/reset/sync'
  'page/sheet/coordinate/refdes'
  'technical-document-intake'
  'hardware-spec-contract-audit'
  'camera-pipeline-contract-audit'
)
CONTENT_OK=1
if [[ ! -f "$SKILL" ]]; then
  CONTENT_OK=0
else
  for token in "${TOKENS[@]}"; do
    rg -qiF "$token" "$SKILL" || CONTENT_OK=0
  done
fi
if [[ "$CONTENT_OK" -eq 1 ]] \
  && head -4 "$SKILL" | rg -q '^description: Use when' \
  && rg -qi 'label.*does not prove.*electrical|标签.*不能证明.*电气' "$SKILL" \
  && ! rg -q 'mcp__|TaskOutput|functions\.exec|collaboration\.' "$SKILL"; then
  ok "技能覆盖证据等级、接口电气条件与标签误判边界"
else
  bad "原理图技能关键边界不完整"
fi

if [[ -f "$SKILL" ]] \
  && rg -qi 'GPIO|I2C|SPI|UART|MIPI|GMSL|FSYNC|PPS' "$SKILL" \
  && rg -qi 'power.*sequence|rail.*sequence' "$SKILL" \
  && rg -qi 'explicit authorization|显式授权' "$SKILL" \
  && rg -qi 'do not.*measure|不.*测量|no hardware mutation' "$SKILL"; then
  ok "总线/同步/供电时序与硬件操作授权边界完整"
else
  bad "原理图接口或安全合同不完整"
fi

if [[ -f "tests/skill-evals/$NAME.json" ]] \
  && [[ -f "tests/skill-evals/results/$NAME-red.md" ]] \
  && [[ -f "tests/skill-evals/results/$NAME-green.md" ]] \
  && python3 - "tests/skill-evals/$NAME.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["skill"] == "schematic-interface-audit"
assert len(d["scenarios"]) >= 4
assert set(d["required_output"]) == {
    "schematic_scope", "source_quality", "sheet_map", "interface_matrix",
    "power_sequence", "ambiguities", "verdict"
}
PY
then ok "原理图技能保留四场景 RED/GREEN eval"; else bad "原理图 eval 证据缺失"; fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
