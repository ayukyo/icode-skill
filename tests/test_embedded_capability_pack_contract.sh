#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

MANIFEST="skill-packs/manifest.json"
ROUTES="mcp/workflow-gate/skill-routes.json"
SKILLS=(
  embedded-runtime-provenance
  camera-pipeline-contract-audit
  embedded-performance-stability
  camera-image-quality-calibration
)

if python3 - "$MANIFEST" "$ROUTES" "${SKILLS[@]}" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
routes = json.load(open(sys.argv[2], encoding="utf-8"))
expected = set(sys.argv[3:])
published = {item["name"] for item in manifest["skills"]}
routed = {item["skill"] for item in routes["routes"]}
assert len(manifest["skills"]) == 16
assert expected <= published
assert expected <= routed
for route in routes["routes"]:
    if route["skill"] in expected:
        assert route["steps"]
        assert route["triggers"]
        assert route["input_contract"]
        assert route["output_contract"]
        assert route["fallback"].strip()
PY
then ok "四个嵌入式/摄像头技能已发布并路由"; else bad "manifest/routes 缺新增技能"; fi

check_skill() {
  local name="$1"; shift
  local file="skill-packs/$name/SKILL.md.template"
  if [[ ! -f "$file" ]]; then
    bad "$name 技能文件存在"
    return
  fi
  local content_ok=1 token
  for token in "$@"; do
    rg -q --fixed-strings "$token" "$file" || content_ok=0
  done
  if [[ "$content_ok" -eq 1 ]] \
    && head -4 "$file" | rg -q '^description: Use when' \
    && ! rg -q 'mcp__|TaskOutput|functions\.exec|collaboration\.' "$file"; then
    ok "$name 有完整输出合同且宿主无关"
  else
    bad "$name 输出合同/触发描述/宿主边界不完整"
  fi
}

check_skill embedded-runtime-provenance \
  'hardware_identity' 'software_baseline' 'component_matrix' \
  'deployment_runtime_matrix' 'mismatches' 'verdict'
check_skill camera-pipeline-contract-audit \
  'pipeline_scope' 'node_matrix' 'edge_contract_matrix' \
  'buffer_ownership' 'breakpoints' 'verdict'
check_skill embedded-performance-stability \
  'performance_budget' 'measurement_matrix' 'resource_timeline' \
  'soak_faults' 'verdict'
check_skill camera-image-quality-calibration \
  'capture_baseline' 'calibration_identity' 'scene_matrix' \
  'metric_matrix' 'comparison_gaps' 'verdict'

if rg -q 'hardware_fault_matrix' skill-packs/state-lifecycle-replay-audit/SKILL.md.template \
  && rg -q 'sensor disconnect|sensor.*disconnect' skill-packs/state-lifecycle-replay-audit/SKILL.md.template \
  && rg -q 'explicit authorization|显式授权' skill-packs/state-lifecycle-replay-audit/SKILL.md.template; then
  ok "生命周期技能覆盖硬件故障恢复"
else
  bad "生命周期技能缺硬件故障矩阵"
fi

if rg -q 'DTS.*Kconfig.*DTB|Kconfig.*DTB' skill-packs/cross-layer-contract-audit/SKILL.md.template \
  && rg -q 'UAPI.*ABI|ABI.*UAPI' skill-packs/cross-layer-contract-audit/SKILL.md.template \
  && rg -q 'embedded-runtime-provenance' skill-packs/cross-layer-contract-audit/SKILL.md.template \
  && rg -q 'camera-pipeline-contract-audit' skill-packs/cross-layer-contract-audit/SKILL.md.template; then
  ok "跨层合同技能覆盖嵌入式边界路由"
else
  bad "跨层合同技能缺嵌入式边界"
fi

EVAL_OK=1
for name in "${SKILLS[@]}"; do
  [[ -f "tests/skill-evals/$name.json" ]] || EVAL_OK=0
  [[ -f "tests/skill-evals/results/$name-red.md" ]] || EVAL_OK=0
  [[ -f "tests/skill-evals/results/$name-green.md" ]] || EVAL_OK=0
done
if [[ "$EVAL_OK" -eq 1 ]]; then ok "四个技能均保留 RED/GREEN eval"; else bad "技能 eval 证据不完整"; fi

TOOL="tools/embedded_profile.py"
SCHEMA="schemas/embedded-baseline.schema.json"
TEMPLATE="templates/embedded_baseline.json.template"
TMP="$(mktemp -d -t icode-embedded-profile.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
TICKET="$TMP/.icode_output/.icode_output_1"
mkdir -p "$TICKET" "$TMP/outside"

if [[ -f "$TOOL" && -f "$SCHEMA" && -f "$TEMPLATE" ]]; then
  ok "baseline schema/template/tool 已提供"
else
  bad "baseline schema/template/tool 缺失"
fi

cat >"$TICKET/embedded_baseline.json" <<'JSON'
{
  "schema_version": 1,
  "project_profile": "camera",
  "identity": {"device_id":"cam-001","board_model":"board-a","board_revision":"r2","soc":"soc-x"},
  "software": {"bootloader":"u-boot-a","kernel":"6.1-a","dtb":"board-a.dtb","rootfs":"rootfs-a","bsp":"bsp-a","toolchain":"gcc-a"},
  "components": [
    {"name":"camera-driver","kind":"kernel_module","identity":"build-1","artifact_path":"/lib/modules/camera.ko","hash":"sha256:aa","build_id":"id-aa","loaded_evidence":"/proc/modules:camera","required":true}
  ],
  "cameras": [
    {"name":"front","sensor":"sensor-a","module":"module-a","lens":"lens-a","eeprom_sn":"sn-a","calibration_hash":"sha256:bb","tuning_hash":"sha256:cc","media_nodes":["/dev/media0","/dev/video0"]}
  ],
  "verification": {
    "scenarios": [
      {"id":"stream","layer":"physical","consumer":"camera_pipeline","required_evidence":["sequence and timestamp trace"],"metrics":[{"name":"fps","unit":"fps","operator":"gte","value":29.0}]}
    ],
    "safety":{"read_only_probe_default":true,"mutations_require_explicit_authorization":true}
  }
}
JSON
BASELINE_BEFORE="$(sha256sum "$TICKET/embedded_baseline.json" | cut -d' ' -f1)"

if [[ -f "$TOOL" ]] \
  && python3 "$TOOL" validate --baseline "$TICKET/embedded_baseline.json" >/dev/null \
  && python3 "$TOOL" plan --baseline "$TICKET/embedded_baseline.json" \
       --output "$TICKET/embedded_verification_plan.json" \
       --markdown "$TICKET/embedded_verification_plan.md" >/dev/null \
  && [[ "$BASELINE_BEFORE" == "$(sha256sum "$TICKET/embedded_baseline.json" | cut -d' ' -f1)" ]] \
  && python3 - "$TICKET/embedded_verification_plan.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["schema_version"] == 1
assert p["read_only"] is True
assert p["source_baseline_sha256"].startswith("sha256:")
c=p["verification_contract"]
assert c["profile"] == "camera"
assert c["baseline_ref"] == p["source_baseline_sha256"]
assert c["required_layers"] == ["physical"]
assert c["required_consumers"] == ["camera_pipeline"]
assert c["required_scenarios"] == ["stream"]
assert c["required_cells"] == [{"layer":"physical","consumer":"camera_pipeline","scenario":"stream"}]
assert c["required_metrics"][0]["name"] == "fps"
PY
then ok "baseline 只读生成结构化验证计划"; else bad "baseline validate/plan 失败"; fi

python3 - "$TICKET/embedded_baseline.json" "$TICKET/unsafe.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
d["verification"]["safety"]["read_only_probe_default"]=False
json.dump(d, open(sys.argv[2], "w", encoding="utf-8"))
PY
if [[ -f "$TOOL" ]] && ! python3 "$TOOL" validate --baseline "$TICKET/unsafe.json" >/dev/null 2>&1; then
  ok "危险 safety 配置被拒绝"
else
  bad "危险 safety 配置未被拒绝"
fi

python3 - "$TICKET/embedded_baseline.json" "$TICKET/sparse.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
d["verification"]["scenarios"].append({
  "id":"algorithm-restart", "layer":"consumption", "consumer":"algorithm",
  "required_evidence":["restart recovery trace"],
  "metrics":[{"name":"recovery_ms","unit":"ms","operator":"lte","value":500.0}]
})
json.dump(d, open(sys.argv[2], "w", encoding="utf-8"))
PY
if python3 "$TOOL" plan --baseline "$TICKET/sparse.json" \
     --output "$TICKET/sparse-plan.json" --markdown "$TICKET/sparse-plan.md" >/dev/null \
  && python3 - "$TICKET/sparse-plan.json" <<'PY'
import json, sys
c=json.load(open(sys.argv[1], encoding="utf-8"))["verification_contract"]
assert c["required_cells"] == [
 {"layer":"physical","consumer":"camera_pipeline","scenario":"stream"},
 {"layer":"consumption","consumer":"algorithm","scenario":"algorithm-restart"},
]
PY
then
  ok "稀疏多场景保持显式 cell 而非笛卡尔积"
else
  bad "稀疏多场景被错误展开"
fi

python3 - "$TICKET/embedded_baseline.json" "$TICKET/no-camera.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8")); d["cameras"]=[]
json.dump(d, open(sys.argv[2], "w", encoding="utf-8"))
PY
if ! python3 "$TOOL" validate --baseline "$TICKET/no-camera.json" >/dev/null 2>&1; then
  ok "camera profile 强制至少一个摄像头身份"
else
  bad "camera profile 接受空 cameras"
fi

python3 - "$TICKET/embedded_baseline.json" "$TICKET/duplicate.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
d["verification"]["scenarios"].append(d["verification"]["scenarios"][0])
json.dump(d, open(sys.argv[2], "w", encoding="utf-8"))
PY
if [[ -f "$TOOL" ]] && ! python3 "$TOOL" validate --baseline "$TICKET/duplicate.json" >/dev/null 2>&1; then
  ok "重复 scenario 被拒绝"
else
  bad "重复 scenario 未被拒绝"
fi

python3 - "$TICKET/embedded_baseline.json" "$TICKET/unknown.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8")); d["unknown_field"]=1
json.dump(d, open(sys.argv[2], "w", encoding="utf-8"))
PY
if [[ -f "$TOOL" ]] && ! python3 "$TOOL" validate --baseline "$TICKET/unknown.json" >/dev/null 2>&1; then
  ok "未知 baseline 字段被拒绝"
else
  bad "未知 baseline 字段未被拒绝"
fi

if [[ -f "$TOOL" ]] \
  && ! python3 "$TOOL" plan --baseline "$TICKET/embedded_baseline.json" \
       --output "$TMP/outside/plan.json" --markdown "$TICKET/ok.md" >/dev/null 2>&1 \
  && ! python3 "$TOOL" plan --baseline "$TICKET/embedded_baseline.json" \
       --output "$TICKET/embedded_baseline.json" --markdown "$TICKET/ok.md" >/dev/null 2>&1; then
  ok "计划输出越界和覆盖输入被拒绝"
else
  bad "计划输出路径保护不完整"
fi

ln -s "$TMP/outside/symlink-plan.json" "$TICKET/symlink-plan.json"
if [[ -f "$TOOL" ]] \
  && ! python3 "$TOOL" plan --baseline "$TICKET/embedded_baseline.json" \
       --output "$TICKET/symlink-plan.json" --markdown "$TICKET/symlink-plan.md" \
       >/dev/null 2>&1; then
  ok "计划输出拒绝符号链接越界"
else
  bad "计划输出接受了符号链接越界"
fi

if [[ -f "$TOOL" ]] && python3 - "$TOOL" "$TICKET" <<'PY'
import importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location("embedded_profile", sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
root=pathlib.Path(sys.argv[2])
json_path=root/"atomic.json"; markdown_path=root/"atomic.md"
json_before=b"\xffold-json"; markdown_before=b"\xfeold-markdown"
json_path.write_bytes(json_before); markdown_path.write_bytes(markdown_before)
real_replace=module.os.replace
failed=False
def fail_second(source, target):
    global failed
    if pathlib.Path(target) == markdown_path and not failed:
        failed=True
        raise OSError("injected second replace failure")
    return real_replace(source, target)
module.os.replace=fail_second
try:
    module.write_pair(json_path, "new-json", markdown_path, "new-markdown")
except OSError:
    pass
else:
    raise AssertionError("injected failure was not raised")
assert json_path.read_bytes() == json_before
assert markdown_path.read_bytes() == markdown_before
assert not list(root.glob(".atomic.*.tmp"))
PY
then
  ok "双文件写入第二步失败时完整回滚"
else
  bad "双文件原子回滚不完整"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
