"""Derived overview refresh never changes workflow authority or command status."""
import importlib.util
import json
import sys
import pytest
from pathlib import Path

from test_delivery_inventory import inventory_repo


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('overview_checkpoint_control', ROOT / 'tools/icode_control.py')
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)


def test_refresh_generates_only_existing_overview_name(inventory_repo):
    repo, path, _ = inventory_repo
    original = path.read_bytes()
    result = ctl.refresh_ticket_overview(path.parent)
    assert result['state'] == 'generated'
    assert (path.parent / 'delivery_validation_report.md').is_file()
    assert not (path.parent / 'delivery_validation_report.json').exists()
    assert path.read_bytes() == original


def test_refresh_failure_is_explicit_and_does_not_recover_transaction(inventory_repo):
    _, path, _ = inventory_repo
    pending = path.parent / ctl.TXN_NAME
    pending.write_text('{}', encoding='utf-8')
    before = pending.read_bytes()
    result = ctl.refresh_ticket_overview(path.parent)
    assert result['state'] == 'unavailable'
    assert pending.read_bytes() == before
    assert not (path.parent / 'delivery_validation_report.md').exists()


def test_completed_dispatch_adds_derived_refresh_without_double_json(inventory_repo, monkeypatch, capsys):
    _, path, _ = inventory_repo
    def completed(args):
        print(json.dumps({'ok': True, 'to': 'completed', 'event_id': 'fixture'}))
        return 0
    monkeypatch.setattr(ctl, 'cmd_transition', completed)
    monkeypatch.setattr(sys, 'argv', ['icode_control.py', 'transition', '--dir', str(path.parent),
                                     '--to', 'completed', '--delivery-verdict', 'verification_pending'])
    assert ctl.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result['overview']['state'] == 'generated'
    assert result['ok'] is True


def test_intermediate_dispatch_does_not_generate_report(inventory_repo, monkeypatch, capsys):
    _, path, _ = inventory_repo
    def intermediate(args):
        print(json.dumps({'ok': True, 'to': 'code_done'}))
        return 0
    monkeypatch.setattr(ctl, 'cmd_transition', intermediate)
    monkeypatch.setattr(sys, 'argv', ['icode_control.py', 'transition', '--dir', str(path.parent), '--to', 'code_done'])
    assert ctl.main() == 0
    assert 'overview' not in json.loads(capsys.readouterr().out)
    assert not (path.parent / 'delivery_validation_report.md').exists()


def test_overview_keeps_full_mode_monotonic(inventory_repo):
    _, path, meta = inventory_repo
    spec = importlib.util.spec_from_file_location('mode_overview_delivery', ROOT / 'tools/icode_delivery.py')
    delivery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delivery)
    meta = dict(meta, mode='full', risk_profile={'policy_version': 2, 'requested_mode': 'fast', 'effective_mode': 'fast'})
    report = delivery.build_delivery_report(path.parent, meta=meta, include_crosscheck=False)
    assert report['mode']['effective'] == 'full'


def test_overview_malformed_profile_has_structured_query_failure(inventory_repo):
    _, path, meta = inventory_repo
    spec = importlib.util.spec_from_file_location('invalid_profile_delivery', ROOT / 'tools/icode_delivery.py')
    delivery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delivery)
    meta = dict(meta, risk_profile=['malformed'])
    with pytest.raises((ValueError, delivery.ctl.ControlError)):
        delivery.build_delivery_report(path.parent, meta=meta, include_crosscheck=False)
