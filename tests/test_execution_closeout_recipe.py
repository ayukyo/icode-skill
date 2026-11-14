"""Execute the documented one-call recipe against ordinary production gates."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def recipe():
    text = (ROOT / 'references/execution_model.md').read_text(encoding='utf-8')
    blocks = re.findall(r'^```bash\n(.*?)^```', text, re.M | re.S)
    selected = [b for b in blocks if b.startswith('# ICODE_CLOSEOUT_PLAN_V1\n')]
    assert len(selected) == 1, 'missing unique tested closeout recipe'
    return selected[0].replace('PY=(python3)', 'PY=(' + shlex.quote(Path(sys.executable).as_posix()) + ')')


def bash():
    if os.name != 'nt':
        value = shutil.which('bash')
    else:
        git = shutil.which('git')
        candidates = [Path(git).parent.parent / 'bin/bash.exe'] if git else []
        candidates += [Path('C:/Program Files/Git/bin/bash.exe'), Path('D:/Program Files/Git/bin/bash.exe')]
        value = next((str(p) for p in candidates if p.is_file()), None)
    if not value:
        pytest.skip('native Bash required for the documented shell recipe')
    return value


@pytest.fixture(params=['full', 'fast'])
def ticket(tmp_path, request):
    out = tmp_path / 'space project/.icode_output/.icode_output_1'
    out.parents[1].mkdir()
    content = '# Complete isolated plan\n'
    seed = {'mode': request.param, 'semantic_decisions': [], 'requirement_deltas': []}
    if request.param == 'fast':
        catalog = json.loads((ROOT / 'mcp/workflow-gate/gates.json').read_text(encoding='utf-8'))
        flags = list(catalog['fast_risk_upgrade']['risk_flags']) + catalog['fast_risk_upgrade']['policy_v2']['full_flags']
        scope = {'summary': 'Isolated bounded recipe fixture', 'source_step': 'plan', 'at': '2026-11-14T00:00:00Z'}
        seed.update(scope_contract=scope, risk_profile={'policy_version': 2, 'requested_mode': 'fast',
            'effective_mode': 'fast', 'override': False, 'triggers': [], 'risk_flags': dict.fromkeys(flags, False),
            'classification': {'scope': 'bounded', 'scope_digest': hashlib.sha256(json.dumps(
                scope, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest(),
                'evidence_refs': [{'scope': 'ticket', 'path': '01_plan.md',
                                   'sha256': hashlib.sha256(content.encode()).hexdigest()}]}})
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    env.pop('ICODE_CONTROL_TEST_MODE', None)

    def control(*args, ok=True):
        proc = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/icode_control.py'),
            *map(str, args), '--dir', str(out)], capture_output=True, text=True, encoding='utf-8', env=env, timeout=60)
        if ok:
            assert proc.returncode == 0, proc.stdout + proc.stderr
        return proc

    control('create', '--ticket-id', 'closeout-recipe', '--birth', 'plan', '--requirement',
            'Isolated fixture, not a semantic model acceptance', '--metadata-json', json.dumps(seed))
    (out / '01_plan.md').write_bytes(content.encode('utf-8'))
    trace = dict(schema_version=1, ticket_id='closeout-recipe', step='plan', tier='L2', default_tier='L2',
        triggers=[], mechanism='sequential-thinking', attempted=True, result='success', degraded_reason=None,
        over_invoked=False, at='2026-11-14T00:00:00Z')
    trace_path = out / '.thinking_gate_trace.jsonl'
    trace_path.write_text(json.dumps(trace) + '\n', encoding='utf-8')
    control('step', '--step', 'plan', '--phase', 'start', '--attempt', 'recipe-attempt')
    control('step', '--step', 'plan', '--phase', 'check', '--attempt', 'recipe-attempt', '--boundary', 'before_write')

    def execute():
        return subprocess.run([bash(), '-s', '--', ROOT.as_posix(), out.as_posix(), 'recipe-attempt', 'recipe'],
            input=recipe(), capture_output=True, text=True, encoding='utf-8', env=env, timeout=90)

    def events():
        raw = [json.loads(line) for line in (out / '.ico_events.jsonl').read_text(encoding='utf-8').splitlines()]
        return [row['value'] if row.get('storage_version') == 2 else row for row in raw]

    return out, control, execute, events, trace_path


def test_one_outer_call_keeps_all_native_receipts_and_normal_validation(ticket):
    _, control, execute, events, _ = ticket
    proc = execute()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    output = [json.loads(part) for part in re.findall(r'(?ms)^\{.*?^\}', proc.stdout)]
    assert len(output) == 4 and all(row['ok'] for row in output)
    rows = events()
    assert all(kind in [row['event_type'] for row in rows] for kind in
               ['artifact_written', 'gate_checked', 'step_finished', 'state_changed'])
    control('validate')


def test_recipe_stops_before_finish_when_output_is_missing(ticket):
    out, _, execute, events, _ = ticket
    (out / '01_plan.md').unlink()
    proc = execute()
    assert proc.returncode != 0
    assert not any(row['event_type'] in ['step_finished', 'state_changed'] for row in events())


def test_late_gate_failure_is_partial_and_resumes_only_the_unfinished_suffix(ticket):
    _, control, execute, events, trace = ticket
    original = trace.read_bytes()
    trace.unlink()
    proc = execute()
    assert proc.returncode != 0
    rows = events()
    assert any(row['event_type'] == 'step_finished' for row in rows)
    assert not any(row['event_type'] == 'state_changed' for row in rows)
    trace.write_bytes(original)
    control('transition', '--to', 'plan_done', '--request', 'recipe.transition')
    control('validate')
