#!/usr/bin/env bash
# Static machine/doc agreement; behavioral assertions are Python regressions.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 - <<'PY'
import json
from pathlib import Path
root = Path.cwd()
model = json.loads((root / 'mcp/workflow-gate/gates.json').read_text())['execution_model']
for step, doc in [('code', '04_code.md'), ('deepcheck', '05_deepcheck.md'), ('audit', '06_audit.md')]:
    ports = model['step_contracts'][step]['outputs']
    port = next(p for p in ports if p['id'] == 'inspection_worklist')
    assert port.get('required', True) and port['kind'] == 'ticket_file'
    assert port['value'] == step + '_worklist.json'
    assert 'inspection_worklist.md' in (root / 'steps' / doc).read_text()
assert 'inspection_worklist.md' in (root / 'steps/crosscheck.md').read_text()
assert 'inspection_worklist.md' in (root / 'SKILL.md').read_text()
assert (root / 'SKILL.md').stat().st_size <= 51200
for name in ['inspection-worklist', 'crosscheck-round', 'crosscheck-manifest']:
    json.loads((root / 'schemas' / (name + '.schema.json')).read_text())
assert 'inspection' not in model['step_contracts'], 'no extra public workflow step'
assert 'crosscheck' not in model['step_contracts'], 'independent review stays isolated'
print('PASS inspection worklist machine/doc/router/compatibility contract')
PY
