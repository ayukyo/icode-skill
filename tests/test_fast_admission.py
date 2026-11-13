"""Code admission verifies actual scope evidence; fixtures are isolated, not reviews."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fast_admission_control', ROOT / 'tools/icode_control.py')
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)


@pytest.fixture
def bounded(tmp_path):
    out = tmp_path / '.icode_output/.icode_output_1'
    out.mkdir(parents=True)
    plan = out / '03_plan_final.md'
    plan.write_text('# Isolated approved-scope fixture\n', encoding='utf-8')
    catalog = json.loads((ROOT / 'mcp/workflow-gate/gates.json').read_text(encoding='utf-8'))
    known = list(catalog['fast_risk_upgrade']['risk_flags']) + catalog['fast_risk_upgrade'].get('policy_v2', {}).get('full_flags', [])
    scope = {'summary': 'Bounded isolated fixture', 'at': '2026-11-13T00:00:00Z', 'source_step': 'plan'}
    digest = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    meta = {'schema_version': 3, 'mode': 'fast', 'project_path': str(tmp_path), 'scope_contract': scope,
            'risk_profile': {'policy_version': 2, 'requested_mode': 'fast', 'effective_mode': 'fast',
                'override': False, 'triggers': [], 'risk_flags': dict.fromkeys(known, False),
                'classification': {'scope': 'bounded', 'scope_digest': digest, 'evidence_refs': [
                    {'scope': 'ticket', 'path': plan.name, 'sha256': hashlib.sha256(plan.read_bytes()).hexdigest()}]}}}
    return out, meta, plan


def test_new_fast_birth_registers_policy_two_but_can_investigate(tmp_path, capsys):
    out = tmp_path / '.icode_output/.icode_output_1'
    args = SimpleNamespace(dir=str(out), ticket_id='FAST-ADMISSION-FIXTURE', requirement='local fix',
                           birth='plan', metadata_json=json.dumps({'mode': 'fast'}), request_id='birth-fast')
    assert ctl.cmd_create(args) == 0
    meta = ctl.load_metadata(out)
    assert meta['risk_profile']['policy_version'] == 2
    assert ctl.verify_event_chain(out, meta)[1] == []
    assert ctl.fast_policy_admission(out, meta, 'plan')['ok']
    assert not ctl.fast_policy_admission(out, meta, 'code')['ok']


def test_malformed_fast_profile_birth_returns_control_error_without_writes(tmp_path):
    out = tmp_path / '.icode_output/.icode_output_1'
    args = SimpleNamespace(dir=str(out), ticket_id='BAD-PROFILE', requirement='fixture',
                           birth='plan', metadata_json=json.dumps({'mode': 'fast', 'risk_profile': ['bad']}),
                           request_id='bad-profile')
    with pytest.raises(ctl.ControlError):
        ctl.cmd_create(args)
    assert not (out / ctl.METADATA_NAME).exists()
    assert not (out / ctl.EVENTS_NAME).exists()


def test_new_ticket_cannot_request_legacy_fast_rules(tmp_path):
    out = tmp_path / '.icode_output/.icode_output_1'
    args = SimpleNamespace(dir=str(out), ticket_id='NO-LEGACY-BIRTH', requirement='fixture',
                           birth='plan', metadata_json=json.dumps({'mode': 'fast', 'risk_profile': {'policy_version': 1}}),
                           request_id='no-legacy')
    with pytest.raises(ctl.ControlError):
        ctl.cmd_create(args)
    assert not (out / ctl.METADATA_NAME).exists()


def test_new_full_ticket_cannot_enter_fast_by_omitting_profile(tmp_path, capsys):
    out = tmp_path / '.icode_output/.icode_output_1'
    args = SimpleNamespace(dir=str(out), ticket_id='FULL-MONOTONIC-BIRTH', requirement='fixture',
                           birth='plan', metadata_json=json.dumps({'mode': 'full'}), request_id='full-birth')
    ctl.cmd_create(args)
    before = ctl.load_metadata(out)
    assert ctl.fast_policy_admission(out, before, 'code')['ok']
    gate = ctl._FAST_CONTRACT_MODULE
    if gate is None:
        spec = importlib.util.spec_from_file_location('full_birth_workflow_contract', ROOT / 'tools/lint_workflow_contract.py')
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
    catalog = json.loads((ROOT / 'mcp/workflow-gate/gates.json').read_text(encoding='utf-8'))
    assert gate.validate_fast_risk(before, catalog) == []
    after = dict(before, mode='fast', risk_profile=None)
    with pytest.raises(ctl.ControlError):
        ctl.protect_fast_policy_update(before, after)


def test_bounded_current_ticket_reference_admits_code(bounded):
    out, meta, _ = bounded
    result = ctl.fast_policy_admission(out, meta, 'code')
    assert result['ok'] and result['route'] == 'fast'


def test_changed_scope_evidence_cannot_admit_code(bounded):
    out, meta, plan = bounded
    plan.write_text('changed after classification', encoding='utf-8')
    result = ctl.fast_policy_admission(out, meta, 'code')
    assert not result['ok'] and 'evidence_hash_mismatch' in result['reasons']


def test_unreadable_and_escaping_references_are_rejected(bounded):
    out, meta, plan = bounded
    meta['risk_profile']['classification']['evidence_refs'][0]['path'] = '../outside.md'
    assert not ctl.fast_policy_admission(out, meta, 'code')['ok']
    meta['risk_profile']['classification']['evidence_refs'][0]['path'] = 'missing.md'
    assert not ctl.fast_policy_admission(out, meta, 'code')['ok']


def test_identity_risk_requires_actual_impact_contract(bounded):
    out, meta, _ = bounded
    meta['risk_profile']['risk_flags']['persistent_identity_change'] = True
    result = ctl.fast_policy_admission(out, meta, 'code')
    assert not result['ok'] and 'missing_identity_impact' in result['reasons']


def test_full_upgrade_keeps_declared_real_environment_contract(bounded):
    out, meta, _ = bounded
    meta['risk_profile']['risk_flags']['real_env_verification'] = True
    meta['risk_profile']['effective_mode'] = 'full'
    result = ctl.fast_policy_admission(out, meta, 'code')
    assert not result['ok'] and 'missing_real_environment_contract' in result['reasons']


def test_lifecycle_risk_requires_actual_acceptance_contract(bounded):
    out, meta, _ = bounded
    meta['risk_profile']['risk_flags']['async_observer_or_cache'] = True
    result = ctl.fast_policy_admission(out, meta, 'code')
    assert not result['ok'] and 'missing_lifecycle_acceptance' in result['reasons']


def test_real_environment_risk_requires_defined_contract_but_no_result_before_code(bounded):
    out, meta, _ = bounded
    meta['risk_profile']['risk_flags']['real_env_verification'] = True
    assert not ctl.fast_policy_admission(out, meta, 'code')['ok']
    meta['verification_contract'] = {'required': True, 'required_layers': ['physical'],
                                    'required_consumers': ['camera'], 'required_scenarios': ['capture']}
    assert ctl.fast_policy_admission(out, meta, 'code')['ok']


def test_legacy_admission_is_unchanged(bounded):
    out, meta, _ = bounded
    meta['risk_profile'].pop('policy_version')
    meta['risk_profile'].pop('classification')
    assert ctl.fast_policy_admission(out, meta, 'code') == {'ok': True, 'legacy': True}


def test_policy_marker_and_effective_full_cannot_be_downgraded(bounded):
    _, meta, _ = bounded
    after = json.loads(json.dumps(meta))
    after['risk_profile'].pop('policy_version')
    with pytest.raises(ctl.ControlError):
        ctl.protect_fast_policy_update(meta, after)
    before = json.loads(json.dumps(meta))
    before['risk_profile']['effective_mode'] = 'full'
    with pytest.raises(ctl.ControlError):
        ctl.protect_fast_policy_update(before, meta)


def test_metadata_writer_cannot_remove_policy_marker(tmp_path, capsys):
    out = tmp_path / '.icode_output/.icode_output_1'
    create = SimpleNamespace(dir=str(out), ticket_id='FAST-POLICY-MARKER', requirement='local fix',
                             birth='plan', metadata_json=json.dumps({'mode': 'fast'}), request_id='birth')
    assert ctl.cmd_create(create) == 0
    before = (out / '.ico_events.jsonl').read_bytes()
    args = SimpleNamespace(dir=str(out), set_json=json.dumps({'risk_profile': {'effective_mode': 'fast'}}),
                           append_json=None, request_id='remove-policy', actor='icode')
    with pytest.raises(ctl.ControlError):
        ctl.cmd_metadata_update(args)
    assert (out / '.ico_events.jsonl').read_bytes() == before
    assert ctl.load_metadata(out)['risk_profile']['policy_version'] == 2


@pytest.mark.parametrize('version', [True, '2', 3])
def test_schema_rejects_invalid_or_unsupported_policy_version(version):
    schema = json.loads((ROOT / 'schemas/ticket-metadata.schema.json').read_text(encoding='utf-8'))
    assert ctl.check_schema({'policy_version': version}, schema['properties']['risk_profile'])


def test_v2_full_mode_is_used_by_coverage_and_control(tmp_path):
    from test_workflow_evidence_gaps import coverage, row, GATE, CATALOG
    metadata = {'mode': 'full', 'risk_profile': {'policy_version': 2,
                'requested_mode': 'fast', 'effective_mode': 'fast'}}
    current = row(eligible=False, decision='skipped_stage_not_reached', attempted=False,
                  evidence={'mode': 'fast', 'phase': 'reverse'})
    report = coverage.build_report(tmp_path, 'deepcheck', False, metadata, [current],
                                   {'gates': [GATE], 'constants': CATALOG['constants']}, [])
    assert report['schema_errors'] == 1 and report['skipped_stage_not_reached'] == 0
    assert ctl.workflow_effective_mode(metadata) == 'full'


def test_scope_reference_budget_is_bounded(bounded):
    out, meta, _ = bounded
    refs = meta['risk_profile']['classification']['evidence_refs']
    meta['risk_profile']['classification']['evidence_refs'] = refs * 33
    result = ctl.fast_policy_admission(out, meta, 'code')
    assert not result['ok'] and 'evidence_budget_exceeded' in result['reasons']


@pytest.mark.parametrize('step', ['review', 'code'])
def test_final_evidence_digest_crossing_deadline_cannot_admit_fast(bounded, monkeypatch, step):
    out, meta, _ = bounded
    clock = [0]
    original = ctl.hashlib.sha256
    class DelayedDigest:
        def __init__(self):
            self.actual = original()
        def update(self, data):
            self.actual.update(data)
        def hexdigest(self):
            value = self.actual.hexdigest()
            clock[0] = 10  # The final digest finishes after the five-second budget.
            return value
    monkeypatch.setattr(ctl.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(ctl.hashlib, 'sha256', lambda *args, **kwargs:
                        original(*args, **kwargs) if args or kwargs else DelayedDigest())
    result = ctl.fast_policy_admission(out, meta, step)
    assert not result['ok'] and 'evidence_budget_exceeded' in result['reasons']


def test_required_contract_validation_finishes_inside_the_same_deadline(bounded, monkeypatch):
    out, meta, _ = bounded
    ctl.fast_policy_admission(out, meta, 'code')  # Load the real contract checker.
    checker = ctl._FAST_CONTRACT_MODULE
    original = checker.validate_identity_change
    clock, calls = [0], []
    def delayed_contract(*args, **kwargs):
        outcome = original(*args, **kwargs)
        calls.append(True)
        clock[0] = 10
        return outcome
    meta['risk_profile']['risk_flags']['persistent_identity_change'] = True
    meta['impact_contract'] = {'identity_change': True}
    monkeypatch.setattr(ctl.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(checker, 'validate_identity_change', delayed_contract)
    result = ctl.fast_policy_admission(out, meta, 'code')
    assert calls == [True]
    assert not result['ok'] and 'evidence_budget_exceeded' in result['reasons']


def test_parent_replaced_by_junction_during_open_cannot_admit_fast(bounded, monkeypatch):
    out, meta, original_plan = bounded
    inside = out / 'evidence'
    inside.mkdir()
    plan = inside / 'scope.md'
    original_plan.rename(plan)
    meta['risk_profile']['classification']['evidence_refs'][0]['path'] = 'evidence/scope.md'
    outside = out.parent.parent / 'moved-evidence-fixture'
    assert inside.resolve().is_relative_to(out.resolve())
    assert outside.resolve().is_relative_to(out.parent.parent.resolve())
    assert not outside.exists()
    original_open, replaced = ctl.os.open, []
    def replace_parent(path, *args, **kwargs):
        if Path(path) == plan and not replaced:
            inside.rename(outside)
            if os.name == 'nt':
                import _winapi
                _winapi.CreateJunction(str(outside), str(inside))
            else:
                inside.symlink_to(outside, target_is_directory=True)
            replaced.append(True)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(ctl.os, 'open', replace_parent)
    try:
        result = ctl.fast_policy_admission(out, meta, 'review')
        assert replaced == [True]
        assert not result['ok'] and 'invalid_evidence_file' in result['reasons']
    finally:
        if inside.is_symlink(): inside.unlink()
        elif inside.exists() and getattr(inside.lstat(), 'st_file_attributes', 0) & 0x400:
            inside.rmdir()  # Remove only the junction, never follow its target.


def test_unrelated_sibling_write_does_not_change_reference_parent_identity(bounded, monkeypatch):
    out, meta, plan = bounded
    original_open = ctl.os.open
    def sibling_write(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if Path(path) == plan:
            (out / 'unrelated-sibling-fixture').write_bytes(b'unrelated')
        return handle
    monkeypatch.setattr(ctl.os, 'open', sibling_write)
    result = ctl.fast_policy_admission(out, meta, 'review')
    assert result['ok'] and result['route'] == 'fast'


@pytest.mark.parametrize('flags', [{}, {'cross_component': False}])
def test_new_full_birth_with_boolean_subset_keeps_full_code_admission(tmp_path, capsys, flags):
    out = tmp_path / '.icode_output/.icode_output_1'
    args = SimpleNamespace(dir=str(out), ticket_id='FULL-SUBSET-FIXTURE', requirement='full fixture',
        birth='plan', metadata_json=json.dumps({'mode': 'full', 'risk_profile': {'risk_flags': flags}}),
        request_id='full-subset')
    assert ctl.cmd_create(args) == 0
    meta = ctl.load_metadata(out)
    assert meta['risk_profile']['policy_version'] == 2
    result = ctl.fast_policy_admission(out, meta, 'code')
    assert result['ok'] and result['route'] == 'full'


@pytest.mark.parametrize('profile', [{'risk_flags': {'real_env_verification': True}},
                                    {'risk_flags': {}, 'triggers': ['real_env_verification']}])
def test_full_subset_declared_risk_still_requires_its_real_contract(tmp_path, capsys, profile):
    out = tmp_path / '.icode_output/.icode_output_1'
    args = SimpleNamespace(dir=str(out), ticket_id='FULL-SUBSET-RISK', requirement='full fixture',
        birth='plan', metadata_json=json.dumps({'mode': 'full', 'risk_profile': profile}),
        request_id='full-subset-risk')
    assert ctl.cmd_create(args) == 0
    result = ctl.fast_policy_admission(out, ctl.load_metadata(out), 'code')
    assert not result['ok'] and result['route'] == 'full'
    assert 'real_environment_verification' in result['required_checks']
    assert 'missing_real_environment_contract' in result['reasons']
