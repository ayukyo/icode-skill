#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PASS=0
FAIL=0
ok() { printf '  PASS %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1" >&2; FAIL=$((FAIL + 1)); }
contains() {
  local file="$1" text="$2" label="$3"
  if rg -qF -- "$text" "$file"; then ok "$label"; else bad "$label (missing: $text)"; fi
}

echo "=== 1. public route and canonical docs ==="
contains SKILL.md '/icode crosscheck' 'SKILL help exposes crosscheck'
contains SKILL.md 'steps/crosscheck.md' 'SKILL routes to crosscheck step'
contains README.md '/icode crosscheck' 'English README exposes crosscheck'
contains README.zh-CN.md '/icode crosscheck' 'Chinese README exposes crosscheck'
contains integrations/codebuddy/commands/icode.md 'crosscheck' 'CodeBuddy bridge can route crosscheck'
contains steps/crosscheck.md '不得读取既往 crosscheck' 'fresh review is independent'
contains references/crosscheck_mode.md '原工单零回写' 'zero-write rule has a canonical reference'
contains references/crosscheck_mode.md 'stale_input' 'input drift rule is documented'

echo "=== 2. standalone schemas and tool ==="
contains schemas/crosscheck-manifest.schema.json '"additionalProperties": false' 'manifest schema is closed'
contains schemas/crosscheck-round.schema.json '"additionalProperties": false' 'round schema is closed'
contains tools/icode_crosscheck.py '"start"' 'tool supports start'
contains tools/icode_crosscheck.py '"freeze"' 'tool supports freeze'
contains tools/icode_crosscheck.py '"finish"' 'tool supports finish'
contains tools/icode_crosscheck.py '"validate"' 'tool supports validate'

echo "=== 3. isolation from ticket lifecycle, UI and runtime ==="
python3 - "$ROOT" <<'PY'
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
host = (root / "agent_runtime/icode_agent/host_runner.py").read_text(encoding="utf-8")
match = re.search(r"ALLOWED_STEPS\s*=\s*frozenset\(\{(.*?)\}\)", host, re.S)
assert match and "crosscheck" not in match.group(1), "Runtime ALLOWED_STEPS must not include crosscheck"

gates = json.loads((root / "mcp/workflow-gate/gates.json").read_text(encoding="utf-8"))
machine = json.dumps(gates.get("state_machine", {}), ensure_ascii=False)
assert "crosscheck" not in machine, "ticket state machine must not include crosscheck"

for name in ("ticket-metadata.schema.json", "ticket-event.schema.json", "ticket-index.schema.json"):
    assert "crosscheck" not in (root / "schemas" / name).read_text(encoding="utf-8"), name

source = (root / "tools/icode_crosscheck.py").read_text(encoding="utf-8")
assert "from icode_control" not in source and "import icode_control" not in source
assert '"resolve-ticket"' in source
for forbidden in ('"transition"', '"index-write"', '"metadata-update"', '"event"'):
    assert forbidden not in source, f"crosscheck must not invoke control writer {forbidden}"
print("  PASS detached command is absent from ticket state, schemas, UI runtime writer path")
PY
PASS=$((PASS + 1))

echo "=== 4. prompt budget and public command spelling ==="
size="$(wc -c < SKILL.md)"
if [[ "$size" -le 51200 ]]; then ok "SKILL remains <= 50KB ($size bytes)"; else bad "SKILL exceeds 50KB ($size bytes)"; fi
contains tests/test_public_command_names_contract.sh '/icode crosscheck' 'public command contract includes crosscheck'

printf '\nRESULT: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
