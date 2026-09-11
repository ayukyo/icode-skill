#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

for path in \
  tools/evidence_intake.py \
  tools/email_intake.py \
  tools/document_intake.py \
  tools/media_router.py \
  tools/debug_catalog.py \
  tools/runtime_baseline.py \
  tools/verification_debt.py \
  tools/learn.py \
  tools/docx/bootstrap_runtime.py \
  tools/docx/build_docx.py \
  tools/docx/inspect_docx.py \
  tools/docx/render_docx.py \
  tools/docx/resolve_renderer.py \
  tools/docx/requirements.lock \
  tools/docx/renderer_manifest.json \
  steps/docx.md \
  steps/learn.md; do
  test -f "$ROOT/$path" || { printf 'missing bundled capability: %s\n' "$path" >&2; exit 1; }
done

rg -q '/icode learn' "$ROOT/SKILL.md"
rg -q '/icode docx' "$ROOT/SKILL.md"
rg -q 'steps/learn\.md' "$ROOT/SKILL.md"
python3 - "$ROOT/mcp/reasoning-gate/gates.json" <<'PY'
import json
import sys

gates = json.load(open(sys.argv[1], encoding="utf-8"))
learn = gates["steps"]["learn"]
assert learn["default_tier"] == "L0"
assert learn["requires_trace"] is False
PY
rg -q 'install/bak/learn' "$ROOT/references/thinking_core.md"
rg -q 'learn.*L0' "$ROOT/references/mcp_per_step.md"
rg -q 'bak/learn/readme' "$ROOT/mcp/sequential-thinking/README.md"
rg -q -- '--pending' "$ROOT/steps/status.md"
rg -q 'verification_debt\.py pending' "$ROOT/steps/status.md"
rg -q -- '/icode verify --plan' "$ROOT/steps/verify.md"
rg -q 'verification_debt\.py plan' "$ROOT/steps/verify.md"
rg -q 'evidence_intake\.py' "$ROOT/steps/log.md"
rg -q 'debug_catalog\.py' "$ROOT/steps/log.md"
rg -q 'runtime_baseline\.py' "$ROOT/steps/log.md"
rg -q 'media_router\.py' "$ROOT/steps/log.md"
rg -q 'submission_guard\.py handoff' "$ROOT/steps/worktree.md"
rg -q '/icode worktree --merge' "$ROOT/steps/worktree.md"
rg -q 'handoff_matrix\.json' "$ROOT/steps/07_readme.md"
rg -q 'icode-mcp-policy/policy\.json' "$ROOT/references/thinking_core.md"
rg -q 'ICODE 本地 MCP 步骤路由' "$ROOT/references/mcp_per_step.md"
python3 - "$ROOT/mcp/icode-mcp-policy/policy.json" <<'PY'
import json
import sys

policy = json.load(open(sys.argv[1], encoding="utf-8"))
expected = {"icode-evidence", "icode-workspace", "icode-device-observe", "icode-mail-observe", "icode-mcp-health", "icode-mcp-policy", "icode-local-index"}
assert set(policy["servers"]) == expected
assert policy["default"] == "deny"
assert any(route["server"] == "icode-mcp-health" for route in policy["steps"]["install"])
assert any(route["server"] == "icode-workspace" for route in policy["steps"]["worktree"])
assert {route["server"] for route in policy["steps"]["docx"]} == {"icode-evidence", "icode-local-index"}
PY

for readme in README.md README.zh-CN.md; do
  rg -q '/icode learn' "$ROOT/$readme"
  rg -q '/icode status \[--pending\]' "$ROOT/$readme"
  rg -q 'handoff' "$ROOT/$readme"
  rg -q 'bundled|内置|随 ICODE' "$ROOT/$readme"
done

# Public installers/sync copy the complete ICODE tree; the new tools are bundled
# commands, while only manifest entries are installed as standalone shared Skills.
rg -q 'rsync' "$ROOT/scripts/sync-to-global.sh"
rg -q 'sync-to-global\.sh' "$ROOT/install.sh"
rg -q 'skill-packs/manifest\.json' "$ROOT/steps/install.md"
if rg -q 'evidence_intake|debug_catalog|runtime_baseline|verification_debt|tools/learn' \
    "$ROOT/skill-packs/manifest.json"; then
  printf 'bundled tools must not be misdeclared as standalone shared Skills\n' >&2
  exit 1
fi

python3 -m py_compile \
  "$ROOT/tools/evidence_intake.py" \
  "$ROOT/tools/email_intake.py" \
  "$ROOT/tools/document_intake.py" \
  "$ROOT/tools/media_router.py" \
  "$ROOT/tools/debug_catalog.py" \
  "$ROOT/tools/runtime_baseline.py" \
  "$ROOT/tools/verification_debt.py" \
  "$ROOT/tools/learn.py" \
  "$ROOT/tools/docx/bootstrap_runtime.py" \
  "$ROOT/tools/docx/build_docx.py" \
  "$ROOT/tools/docx/inspect_docx.py" \
  "$ROOT/tools/docx/resolve_renderer.py" \
  "$ROOT/tools/docx/render_docx.py" \
  "$ROOT/scripts/submission_guard.py"

printf 'PASS: capability routing and bundling contract\n'
