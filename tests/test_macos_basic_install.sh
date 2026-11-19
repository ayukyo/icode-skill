#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX="$(mktemp -d "/private/tmp/icode macOS 验收.XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT

PYTHON_BIN="${ICODE_PYTHON_BIN:-$(command -v python3 || command -v python)}"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "python3 or python is required" >&2
  exit 1
fi
SKILLS_ROOT="$SANDBOX/测试 skills"
RUNTIME_ROOT="${ICODE_DOCX_RUNTIME_ROOT:-$SANDBOX/docx-runtime}"
RENDERER_ROOT="$SANDBOX/renderer"
export AGENTS_SKILLS_ROOT="$SKILLS_ROOT"
export ICODE_PYTHON_BIN="$PYTHON_BIN"
export ICODE_DOCX_RUNTIME_ROOT="$RUNTIME_ROOT"
export ICODE_DOCX_RENDERER_ROOT="$RENDERER_ROOT"
export ICODE_DOCX_RENDERER_CATALOG="$ROOT/tools/docx/renderer_packages.lock.json"

"$ROOT/install.sh" --client codex --skip-mcp
INSTALLED="$SKILLS_ROOT/icode"
test -s "$INSTALLED/SKILL.md"
test -s "$INSTALLED/steps/fast.md"
test -s "$INSTALLED/steps/lite.md"
"$PYTHON_BIN" "$INSTALLED/tools/icode_control.py" --help >/dev/null
test -s "$RUNTIME_ROOT"/*/runtime_manifest.json

"$ROOT/install.sh" --client codex --skip-mcp
"$PYTHON_BIN" "$INSTALLED/tools/validate_skill_pack.py" \
  --manifest "$INSTALLED/skill-packs/manifest.json" \
  --target-root "$SKILLS_ROOT"
test ! -e "$SANDBOX/.claude.json"

echo "macOS basic installation smoke passed"
