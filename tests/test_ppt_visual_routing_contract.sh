#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

STEP="steps/ppt.md"
README="tools/ppt/README.md"
CHECKER="tools/ppt/scripts/check_render_output.py"
TMP="$(mktemp -d -t icode-ppt-visual.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

if [[ -f "$CHECKER" ]] \
  && rg -q 'media_routing\.md' "$STEP" \
  && rg -q 'native_media_injection_allowed' "$STEP" \
  && rg -q '禁止.*Read.*slide-.*\.png' "$STEP" \
  && rg -q 'generation_status.*render_status.*visual_status' "$STEP" \
  && rg -q 'visual_status=unobserved' "$STEP" \
  && rg -q 'Model only support text input' "$STEP" \
  && rg -q 'check_render_output\.py' "$README"
then
  ok "PPT 步骤接入媒体路由、纯文本熔断和分层结论"
else
  bad "PPT 视觉路由合同不完整"
fi

if python3 - "$TMP" <<'PY'
import base64
import sys
from pathlib import Path
from pptx import Presentation

root = Path(sys.argv[1])
deck = Presentation()
deck.slides.add_slide(deck.slide_layouts[6])
deck.slides.add_slide(deck.slide_layouts[6])
deck.save(root / "deck.pptx")
preview = root / "preview"
preview.mkdir()
png = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
for number in (1, 2):
    (preview / f"slide-{number}.png").write_bytes(png)
PY
then :; else bad "PPT 结构测试夹具创建失败"; fi

if python3 "$CHECKER" "$TMP/deck.pptx" "$TMP/preview" >"$TMP/pass.json" \
  && python3 - "$TMP/pass.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["status"] == "passed"
assert d["slide_count"] == 2 and d["preview_count"] == 2
assert d["missing"] == [] and d["invalid"] == [] and d["extras"] == []
assert all(item["width"] == 1 and item["height"] == 1 for item in d["previews"])
PY
then
  ok "纯文本模型可确定性验证 PPT→PNG 数量、签名和尺寸"
else
  bad "PPT 渲染结构检查器未通过正常场景"
fi

mv "$TMP/preview/slide-1.png" "$TMP/preview/slide-01.png"
mv "$TMP/preview/slide-2.png" "$TMP/preview/slide-02.png"
if python3 "$CHECKER" "$TMP/deck.pptx" "$TMP/preview" >"$TMP/padded.json" \
  && python3 - "$TMP/padded.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["status"] == "passed"
assert [item["page"] for item in d["previews"]] == [1, 2]
PY
then
  ok "兼容 pdftoppm 的零填充 slide-01.png 命名"
else
  bad "零填充预览页命名被错误拒绝"
fi

mv "$TMP/preview/slide-02.png" "$TMP/preview/slide-03.png"
if python3 "$CHECKER" "$TMP/deck.pptx" "$TMP/preview" >"$TMP/fail.json" 2>/dev/null; then
  bad "PPT 渲染结构检查器错误放过缺页/多页"
elif python3 - "$TMP/fail.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["status"] == "failed"
assert d["missing"] == ["slide-2.png"]
assert d["extras"] == ["slide-03.png"]
PY
then
  ok "渲染结构检查器对缺页和额外页 fail-closed"
else
  bad "渲染结构检查器失败结果不可审计"
fi

printf 'not-a-pptx' >"$TMP/broken.pptx"
if python3 "$CHECKER" "$TMP/broken.pptx" "$TMP/preview" >"$TMP/broken.json" 2>/dev/null; then
  bad "PPT 渲染结构检查器错误放过损坏文件"
elif python3 - "$TMP/broken.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["status"] == "failed" and d["errors"]
PY
then
  ok "损坏 PPTX 返回可审计 JSON 而非堆栈中断"
else
  bad "损坏 PPTX 未返回可审计失败结果"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
