"""Isolated state/reader continuity; not six-stage reasoning acceptance.

The explicit test-only transition flag builds a completed state fixture. Actual
gate rejection and Read requirements are tested in their dedicated suites.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from test_crosscheck import workspace, run_tool, tree_digest


ROOT = Path(__file__).resolve().parents[1]


def test_new_fast_completed_patch_then_independent_crosscheck(workspace):
    root, _, env = workspace
    env = dict(env, ICODE_CONTROL_TEST_MODE='1', PYTHONUTF8='1')
    ticket = root / '.icode_output/.icode_output_2'
    catalog = json.loads((ROOT / 'mcp/workflow-gate/gates.json').read_text(encoding='utf-8'))
    known = list(catalog['fast_risk_upgrade']['risk_flags']) + catalog['fast_risk_upgrade']['policy_v2']['full_flags']
    plan = '# Final plan\n\nBounded fixture change.\n'
    scope = {'summary': 'bounded fixture', 'at': '2026-11-14T00:00:00Z', 'source_step': 'plan'}
    seed = {'mode': 'fast', 'scope_contract': scope, 'code_files': ['src/feature.c'], 'risk_profile': {
        'risk_flags': dict.fromkeys(known, False), 'override': False, 'triggers': [],
        'classification': {'scope': 'bounded', 'scope_digest': hashlib.sha256(json.dumps(
            scope, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'evidence_refs': [{'scope': 'ticket', 'path': '03_plan_final.md',
                               'sha256': hashlib.sha256(plan.encode()).hexdigest()}]}}}

    def control(*args):
        proc = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'tools/icode_control.py'),
                               *map(str, args), '--dir', str(ticket)], env=env,
                              capture_output=True, text=True, encoding='utf-8', timeout=60)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        value = json.loads(proc.stdout)
        assert value['ok'] is True
        return value

    control('create', '--birth', 'plan', '--ticket-id', 'FAST-CONTINUITY', '--requirement', 'fixture',
            '--metadata-json', json.dumps(seed))
    (ticket / '03_plan_final.md').write_text(plan, encoding='utf-8')
    (ticket / '04_code_review_fix.md').write_text('# Code\nFixture only.\n', encoding='utf-8')
    for state in ('plan_done', 'review_in_progress', 'review_done', 'plan_finalized',
                  'code_in_progress', 'code_done', 'deepcheck_in_progress', 'deepcheck_done', 'completed'):
        verdict = ('--delivery-verdict', 'verification_pending') if state == 'completed' else ()
        control('transition', '--to', state, '--skip-gates', *verdict)

    spec = importlib.util.spec_from_file_location('continuity_context', ROOT / 'tools/ticket_context.py')
    context = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(context)
    before = context.build_ticket_context(ticket)
    assert before['mode']['effective'] == 'fast' and before['status'] == 'completed'
    assert before['overview']['state'] == 'current'
    (ticket / '08_patch.md').write_text('# Patch 1\nAdditional fixture note.\n', encoding='utf-8')
    control('metadata-update', '--append-json', json.dumps({'patch_history': [
        {'patch_no': 1, 'summary': 'fixture patch', 'files': ['src/feature.c']}]}))
    after = context.build_ticket_context(ticket)
    assert after['overview']['state'] == 'stale'
    assert after['latest_patch']['patch_no'] == 1
    assert after['mode']['effective'] == 'fast' and after['status'] == 'completed'
    source_before = tree_digest(root, exclude_crosscheck=True)
    started = run_tool(env, 'start', '--workspace', root, '--ticket', 'FAST-CONTINUITY')
    assert started['round'] == 1
    assert tree_digest(root, exclude_crosscheck=True) == source_before
    fresh = context.build_ticket_context(ticket, purpose='crosscheck_fresh')
    assert fresh['overview']['state'] == 'withheld_for_fresh'
    assert tree_digest(root, exclude_crosscheck=True) == source_before
