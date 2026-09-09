#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

NAME="mcu-hardware-software-contract-audit"
SKILL="skill-packs/$NAME/SKILL.md.template"

if python3 - "$NAME" <<'PY'
import json, sys
name=sys.argv[1]
manifest=json.load(open("skill-packs/manifest.json", encoding="utf-8"))
routes=json.load(open("mcp/workflow-gate/skill-routes.json", encoding="utf-8"))
assert len(manifest["skills"]) == 15
assert name in {item["name"] for item in manifest["skills"]}
route=next(item for item in routes["routes"] if item["skill"] == name)
assert {"plan", "code", "deepcheck", "audit", "verify"} <= set(route["steps"])
assert set(route["output_contract"]) >= {
    "mcu_scope", "pin_signal_matrix", "clock_power_reset_matrix",
    "register_sdk_code_trace", "interrupt_dma_runtime_matrix",
    "variant_compatibility", "verification_plan", "verdict"
}
assert route["triggers"] and route["input_contract"] and route["fallback"]
PY
then ok "MCU 能力已作为第十五个共享技能发布并路由"; else bad "MCU manifest/routes 合同缺失"; fi

TOKENS=(
  mcu_scope pin_signal_matrix clock_power_reset_matrix register_sdk_code_trace
  interrupt_dma_runtime_matrix variant_compatibility verification_plan verdict
  'package pin' 'AF/remap' 'clock gate' 'reset' 'SVD' 'IRQ/vector/NVIC'
  'DMA request/channel' 'buffer owner/lifetime' 'linker script' 'binary hash'
  'programmer target' 'runtime-loaded' 'errata' 'difference note'
  technical-document-intake schematic-interface-audit multi-repo-artifact-provenance
)
CONTENT_OK=1
if [[ ! -f "$SKILL" ]]; then
  CONTENT_OK=0
else
  for token in "${TOKENS[@]}"; do rg -qiF "$token" "$SKILL" || CONTENT_OK=0; done
fi
if [[ "$CONTENT_OK" -eq 1 ]] \
  && head -4 "$SKILL" | rg -q '^description: Use when' \
  && rg -qi 'explicit authorization|显式授权' "$SKILL" \
  && ! rg -q 'mcp__|TaskOutput|functions\.exec|collaboration\.' "$SKILL"; then
  ok "MCU 技能覆盖 datasheet→原理图→SDK→IRQ/DMA→烧录→运行全链"
else
  bad "MCU 技能链路或宿主无关边界不完整"
fi

if [[ -f "tests/skill-evals/$NAME.json" ]] \
  && [[ -f "tests/skill-evals/results/$NAME-red.md" ]] \
  && [[ -f "tests/skill-evals/results/$NAME-green.md" ]] \
  && python3 - "$NAME" <<'PY'
import json, sys
d=json.load(open(f"tests/skill-evals/{sys.argv[1]}.json", encoding="utf-8"))
assert d["skill"] == sys.argv[1]
assert len(d["scenarios"]) >= 4
assert set(d["required_output"]) == {
    "mcu_scope", "pin_signal_matrix", "clock_power_reset_matrix",
    "register_sdk_code_trace", "interrupt_dma_runtime_matrix",
    "variant_compatibility", "verification_plan", "verdict"
}
PY
then ok "MCU 技能保留四场景 RED/GREEN eval"; else bad "MCU eval 证据不完整"; fi

if rg -qF 'mcu-hardware-software-contract-audit' README.md README.zh-CN.md SKILL.md \
     references/skill_routing.md \
  && rg -qi 'vendor.*package|SDK.*tool' \
     skill-packs/multi-repo-artifact-provenance/SKILL.md.template \
  && rg -qF 'vendor_package_matrix' \
     skill-packs/multi-repo-artifact-provenance/SKILL.md.template; then
  ok "ICODE 文档和供应商 SDK/工具溯源能力已接入"
else
  bad "MCU 文档或供应商包溯源接入遗漏"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
