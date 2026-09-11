#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$ROOT/tools/docx"
TMP="$(mktemp -d -t icode-docx-contract.XXXXXX)"
TEST_PYTHON="${ICODE_DOCX_TEST_PYTHON:-python3}"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
ok() { printf '  PASS %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1" >&2; FAIL=$((FAIL + 1)); }

for file in requirements.lock bootstrap_runtime.py build_docx.py inspect_docx.py resolve_renderer.py render_docx.py renderer_manifest.json README.md; do
  if [[ -f "$TOOLS/$file" ]]; then ok "bundled DOCX payload contains $file"; else bad "missing DOCX payload $file"; fi
done

if python3 "$ROOT/tests/test_docx_runtime_download_policy.py"; then
  ok "DOCX runtime package-index fallback is bounded and respects user configuration"
else
  bad "DOCX runtime package-index fallback is bounded and respects user configuration"
fi

RUNTIME_JSON="$(ICODE_DOCX_RUNTIME_ROOT="$TMP/runtime" ICODE_DOCX_RUNTIME_SYSTEM_SITE_PACKAGES=1 "$TEST_PYTHON" "$TOOLS/bootstrap_runtime.py" --python "$(command -v "$TEST_PYTHON")")"
DOCX_PYTHON="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["python"])' "$RUNTIME_JSON")"
if [[ -x "$DOCX_PYTHON" ]] && "$DOCX_PYTHON" -c 'import docx, PIL'; then
  ok "self-managed DOCX runtime installs pinned dependencies"
else
  bad "self-managed DOCX runtime installs pinned dependencies"
fi

SOURCE="$TMP/guide.md"
OUTPUT="$TMP/guide.docx"
MANIFEST="$TMP/guide.manifest.json"
cat >"$SOURCE" <<'MARKDOWN'
# 发布指南

这是带有 **格式** 的交付正文。

## 版本表

| 项目 | 状态 |
|---|---|
| DOCX | 已生成 |

```mermaid
flowchart LR
Source[Markdown 源] --> Build[ICODE 构建]
Build --> Word[DOCX 交付]
```

![logo](tiny.png)

- 保留表格
- Mermaid 成为 DOCX 图片

```bash
./install.sh --skip-mcp
```
MARKDOWN
base64 -d >"$TMP/tiny.png" <<'PNG'
iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9JMTkAAAAASUVORK5CYII=
PNG
SOURCE_HASH="$(sha256sum "$SOURCE" | cut -d' ' -f1)"

if "$DOCX_PYTHON" "$TOOLS/build_docx.py" "$SOURCE" "$OUTPUT" --manifest "$MANIFEST" >/dev/null \
  && "$DOCX_PYTHON" "$TOOLS/inspect_docx.py" "$OUTPUT" --source "$SOURCE" --manifest "$MANIFEST" >/dev/null \
  && [[ "$SOURCE_HASH" == "$(sha256sum "$SOURCE" | cut -d' ' -f1)" ]]; then
  ok "P0 build preserves source and passes structural QA"
else
  bad "P0 build preserves source and passes structural QA"
fi

if "$DOCX_PYTHON" - "$MANIFEST" "$OUTPUT" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert manifest["input"]["sha256"]
assert manifest["output"]["sha256"]
assert manifest["structural_qa"]["passed"] is True
assert any(item["kind"] == "table" for item in manifest["source_map"])
assert any(item["kind"] == "mermaid" and item["rendered_as"] == "image" for item in manifest["source_map"])
assert Path(sys.argv[2]).is_file()
PY
then
  ok "delivery manifest records hashes, source map and Mermaid image"
else
  bad "delivery manifest records hashes, source map and Mermaid image"
fi

if "$DOCX_PYTHON" "$TOOLS/render_docx.py" "$OUTPUT" "$TMP/preview" --result-manifest "$MANIFEST" >/dev/null \
  && "$DOCX_PYTHON" - "$MANIFEST" <<'PY'
import json
import sys
from pathlib import Path
assert json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["visual_qa"]["status"] == "visual_qa_pending"
PY
then
  ok "no bundled renderer is explicit visual_qa_pending without PATH fallback"
else
  bad "no bundled renderer is explicit visual_qa_pending without PATH fallback"
fi

if python3 "$TOOLS/resolve_renderer.py" | python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "unavailable"'; then
  ok "renderer resolver is deterministic and host-LibreOffice-free"
else
  bad "renderer resolver is deterministic and host-LibreOffice-free"
fi

if rg -q '/icode docx' "$ROOT/SKILL.md" "$ROOT/README.md" "$ROOT/README.zh-CN.md" \
  && rg -q 'visual_qa_pending' "$ROOT/steps/docx.md" "$ROOT/tools/docx/README.md" \
  && rg -q '"docx"' "$ROOT/mcp/icode-mcp-policy/policy.json"; then
  ok "public route, MCP policy and visual boundary are documented"
else
  bad "public route, MCP policy and visual boundary are documented"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
