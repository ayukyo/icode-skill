#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

TOOL="tools/media_router.py"
POLICY="references/media_routing.md"
TEMPLATE="templates/media_policy.json.template"
BRIDGE_CONFIG="mcp/vision-bridge/config.example.json"
BRIDGE_README="mcp/vision-bridge/README.md"
HOST_ADAPTERS="references/host_adapters.md"
LOG_STEP="steps/log.md"
TMP="$(mktemp -d -t icode-media-routing.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

cat >"$TMP/profile.json" <<'JSON'
{
  "provider": "weak-bridge",
  "model": "vision-small",
  "api_key": "never-echo-this",
  "profile_version": "eval-1",
  "max_images_per_message": 3,
  "declared_capabilities": ["ocr"],
  "quality_profile": {
    "evaluation_status": "passed-ocr-only",
    "token": "also-never-echo",
    "nested": {"password": "never"}
  }
}
JSON

if [[ -f "$TOOL" && -f "$POLICY" && -f "$TEMPLATE" ]] \
  && python3 "$TOOL" route --native supported --bridge available \
       --native-max-images-per-message 2 --task schematic \
       --bridge-profile "$TMP/profile.json" >"$TMP/native.json" \
  && python3 - "$TMP/native.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
assert d["selected_mode"] == "native"
assert d["status"] == "ready"
assert d["primary_channel"] == "native"
assert d["native_media_injection_allowed"] is True
assert d["image_batching"]["channel_limits"] == {"native": 2, "bridge": 3}
assert d["image_batching"]["selected_max_images_per_message"] == 2
assert d["image_batching"]["strategy"] == "serial_batches_then_text_aggregate"
raw=open(sys.argv[1], encoding="utf-8").read()
assert "never-echo-this" not in raw and "also-never-echo" not in raw
assert "nested" not in d["bridge_profile"]["quality_profile"]
PY
then ok "原生多模态宿主不会被弱 bridge 降级"; else bad "原生优先路由或脱敏失败"; fi

if python3 "$TOOL" route --native supported --bridge available --risk high \
     --task schematic --bridge-profile "$TMP/profile.json" >"$TMP/dual.json" \
  && python3 - "$TMP/dual.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
assert d["selected_mode"] == "dual"
assert d["primary_channel"] == "native" and d["secondary_channel"] == "bridge"
assert d["status"] == "partial"
assert d["bridge_qualification"] == "unqualified"
assert d["image_batching"]["selected_max_images_per_message"] == 3
assert set(d["missing_bridge_capabilities"]) == {"schematic", "spatial_reasoning"}
assert "candidate evidence" in d["claim_ceiling"]
assert any("disagreement" in item for item in d["rationale"])
PY
then ok "高风险双通道路由保留弱模型结论上限和分歧"; else bad "双通道路由边界错误"; fi

if python3 "$TOOL" route --native unsupported --bridge available --task ocr \
     --bridge-profile "$TMP/profile.json" >"$TMP/bridge.json" \
  && python3 "$TOOL" route --native unknown --bridge unavailable --task diagram \
     >"$TMP/text.json" \
  && python3 - "$TMP/bridge.json" "$TMP/text.json" <<'PY'
import json, sys
bridge=json.load(open(sys.argv[1], encoding="utf-8"))
text=json.load(open(sys.argv[2], encoding="utf-8"))
assert bridge["selected_mode"] == "bridge" and bridge["status"] == "ready"
assert bridge["bridge_qualification"] == "qualified"
assert bridge["image_batching"]["selected_max_images_per_message"] == 3
assert text["selected_mode"] == "text_only"
assert text["status"] == "manual_visual_gap"
assert text["image_batching"]["selected_max_images_per_message"] is None
assert "visual or spatial claims remain unresolved" in text["claim_ceiling"]
PY
then ok "纯文本宿主安全走 bridge 或显式视觉缺口"; else bad "bridge/text_only 降级错误"; fi

if python3 "$TOOL" route --mode native --native unknown --bridge available \
     --task general >"$TMP/blocked.json" 2>/dev/null; then
  bad "未知宿主错误允许强制 native"
elif python3 - "$TMP/blocked.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
assert d["status"] == "blocked"
assert d["native_media_injection_allowed"] is False
PY
then ok "未知宿主拒绝试传式 native 探测"; else bad "blocked 路由输出不可审计"; fi

printf 'image-bytes' >"$TMP/page.png"
ln -s "$TMP/page.png" "$TMP/link.png"
if python3 "$TOOL" evidence --path "$TMP/page.png" --media-kind pdf_page \
     --channel native --provider host --model gpt --prompt-profile schematic-v1 \
     --profile-version 1 --page 9 --crop 10,20,300,400 --dpi 300 --tile-index 2 \
     >"$TMP/evidence.json" \
  && ! python3 "$TOOL" evidence --path "$TMP/link.png" --media-kind image \
     --channel native --provider host --model gpt --prompt-profile general-v1 \
     --profile-version 1 >/dev/null 2>&1 \
  && python3 - "$TMP/evidence.json" <<'PY'
import hashlib, json, pathlib, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
assert d["input_sha256"] == "sha256:" + hashlib.sha256(b"image-bytes").hexdigest()
assert d["page"] == 9 and d["crop"] == [10,20,300,400]
assert d["dpi"] == 300 and d["tile_index"] == 2
assert d["channel"] == "native" and d["disagreement"] is None
assert pathlib.Path(d["source_path"]).is_absolute()
PY
then ok "视觉证据绑定 hash/页/裁剪/DPI/通道且拒绝符号链接"; else bad "视觉证据来源合同失败"; fi

if python3 "$TOOL" tiles --width 3000 --height 2300 --tile-size 1536 \
     --overlap 128 >"$TMP/tiles.json" \
  && python3 - "$TMP/tiles.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
tiles=d["tiles"]
assert len(tiles) > 1
assert len({tuple(t["crop"]) for t in tiles}) == len(tiles)
assert min(t["crop"][0] for t in tiles) == 0
assert min(t["crop"][1] for t in tiles) == 0
assert max(t["crop"][0]+t["crop"][2] for t in tiles) == 3000
assert max(t["crop"][1]+t["crop"][3] for t in tiles) == 2300
PY
then ok "高 DPI 页面可生成确定性重叠切片"; else bad "切片覆盖不完整"; fi

if python3 -m py_compile "$TOOL" mcp/vision-bridge/server.py \
     mcp/vision-bridge/providers/base.py mcp/vision-bridge/providers/openai_compat.py \
     mcp/vision-bridge/providers/local_ocr.py \
  && PYTHONPATH=mcp/vision-bridge python3 - <<'PY'
from providers.base import MediaProvider
class P(MediaProvider):
    name="p"
    model="m"
    declared_capabilities=["ocr", "ocr", 7]
    quality_profile={"score": 8, "api_key": "secret", "nested": {"token":"secret"}}
    async def analyze(self, media_path, prompt, media_type, max_tokens=1024):
        return "ok"
d=P().capability_profile()
assert d["declared_capabilities"] == ["ocr"]
assert d["quality_profile"] == {"score": 8}
assert d["transport_limits"] == {}
assert "secret" not in repr(d)
PY
then ok "vision-bridge 能力画像脱敏且代码可解析"; else bad "vision-bridge 能力画像合同失败"; fi

if PYTHONPATH=mcp/vision-bridge python3 - <<'PY'
import asyncio
import json
import os
import pathlib
import tempfile

import server
from providers.openai_compat import OpenAICompatProvider

with tempfile.TemporaryDirectory() as td:
    missing = pathlib.Path(td) / "missing.json"
    os.environ["VISION_BRIDGE_CONFIG"] = str(missing)
    assert server.get_provider().name == "unconfigured"

    invalid = pathlib.Path(td) / "invalid.json"
    invalid.write_text("[]", encoding="utf-8")
    os.environ["VISION_BRIDGE_CONFIG"] = str(invalid)
    try:
        server.load_config()
    except ValueError as exc:
        assert "object" in str(exc)
    else:
        raise AssertionError("non-object config accepted")

    bad_provider = pathlib.Path(td) / "bad-provider.json"
    bad_provider.write_text(json.dumps({"provider": 7}), encoding="utf-8")
    os.environ["VISION_BRIDGE_CONFIG"] = str(bad_provider)
    try:
        server.get_provider()
    except ValueError as exc:
        assert "provider" in str(exc)
    else:
        raise AssertionError("non-string provider accepted")

for field, value in (("timeout", 0), ("video_frames", 0),
                     ("max_images_per_message", 0),
                     ("max_images_per_message", 121),
                     ("max_images_per_message", True)):
    config = {
        "base_url": "https://example.invalid/v1",
        "api_key": "test-only",
        "model": "test-model",
        field: value,
    }
    try:
        OpenAICompatProvider(config)
    except ValueError:
        pass
    else:
        raise AssertionError(f"invalid {field} accepted")

for media_type, max_tokens in (("audio", 10), ("image", 0)):
    try:
        server._validate_analysis_request(media_type, max_tokens)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid media request accepted")
PY
then ok "vision-bridge 配置与媒体边界 fail-closed"; else bad "vision-bridge 配置与媒体边界校验失败"; fi

if PYTHONPATH=mcp/vision-bridge python3 - <<'PY'
import asyncio

from providers.openai_compat import OpenAICompatProvider


class ProbeProvider(OpenAICompatProvider):
    def __init__(self, frame_count, max_images_per_message=4):
        super().__init__({
            "base_url": "https://example.invalid/v1",
            "api_key": "test-only",
            "model": "test-model",
            "video_frames": frame_count,
            "max_images_per_message": max_images_per_message,
        })
        self.calls = []

    def _extract_video_frames(self, video_path):
        return [f"data:image/jpeg;base64,{index}" for index in range(self.video_frames)]

    async def _chat(self, content, max_tokens):
        self.calls.append(content)
        return f"result-{len(self.calls)}"


for frame_count, expected_image_counts in (
        (1, [1]), (4, [4]), (5, [4, 1, 0]),
        (6, [4, 2, 0]), (8, [4, 4, 0])):
    provider = ProbeProvider(frame_count)
    asyncio.run(provider._video("unused.mp4", "按时序分析", 128))
    image_counts = [sum(item.get("type") == "image_url" for item in call)
                    for call in provider.calls]
    assert image_counts == expected_image_counts, (frame_count, image_counts)
    assert all(count <= provider.max_images_per_message for count in image_counts)
    assert provider.capability_profile()["transport_limits"] == {
        "max_images_per_message": 4,
    }
    if frame_count > 4:
        assert "批次 1/" in provider.calls[0][-1]["text"]
        assert provider.calls[-1][0]["type"] == "text"

custom = ProbeProvider(6, max_images_per_message=2)
asyncio.run(custom._video("unused.mp4", "", 128))
assert [sum(item.get("type") == "image_url" for item in call)
        for call in custom.calls] == [2, 2, 2, 0]
PY
then ok "vision-bridge 视频帧按单消息上限分批并以纯文本聚合"; else bad "vision-bridge 图片分批合同失败"; fi

if python3 - "$TEMPLATE" "$BRIDGE_CONFIG" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
assert d["default_max_images_per_message"] == 4
cfg=json.load(open(sys.argv[2], encoding="utf-8"))
assert cfg["max_images_per_message"] == 4
PY
then ok "媒体策略和 bridge 配置声明保守单消息图片上限"; else bad "媒体策略或 bridge 配置缺单消息图片上限"; fi

if rg -q 'max_images_per_message' "$POLICY" \
  && rg -q 'selected_max_images_per_message' "$LOG_STEP" \
  && rg -q 'max_images_per_message' "$BRIDGE_README" \
  && rg -q '串行' "$HOST_ADAPTERS"
then ok "native/bridge/dual 文档都声明单消息限额与串行分批"; else bad "媒体分批文档合同不完整"; fi

if TMP_HOME="$TMP/vision-home" \
   VISION_BRIDGE_TARGET="$TMP/vision-bridge" \
   bash -c 'mkdir -p "$HOME" "$VISION_BRIDGE_TARGET"; echo stale >"$VISION_BRIDGE_TARGET/stale"; HOME="$TMP_HOME" bash mcp/vision-bridge/uninstall.sh --purge >/dev/null; test ! -e "$VISION_BRIDGE_TARGET"'
then ok "vision-bridge --purge 真正清理受限 target"; else bad "vision-bridge --purge 行为与 README 不一致"; fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
