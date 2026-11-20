#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX="$(mktemp -d "/private/tmp/icode macOS 验收.XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT

_python_310_or_newer() {
  [[ "$("$1" -c 'import sys; print(int(sys.version_info >= (3, 10)))' 2>/dev/null)" == "1" ]]
}
if [[ -n "${ICODE_PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$ICODE_PYTHON_BIN"
  if ! _python_310_or_newer "$PYTHON_BIN"; then
    echo "ICODE_PYTHON_BIN must point to Python 3.10 or newer" >&2
    exit 1
  fi
else
  PYTHON_BIN=""
  for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && _python_310_or_newer "$(command -v "$candidate")"; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done
  if [[ -z "$PYTHON_BIN" ]]; then
    echo "Python 3.10 or newer is required (set ICODE_PYTHON_BIN to select it)" >&2
    exit 1
  fi
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
