"""Discovery authenticates stage history without validating unrelated old work."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ordinary_project():
    spec = importlib.util.spec_from_file_location("discovery_stage_fixture", ROOT / "tests/test_stage_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    case = module.StageAcceptanceTests()
    case.setUp()
    try:
        yield case
    finally:
        case.doCleanups()


def damage_unrelated_metric(case):
    path = case.first / case.ctl.METADATA_NAME
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["ticket_id"] = "copied-malformed-ticket"
    metadata["verification_contract"] = {"required": True, "required_metrics": [{"value": True}]}
    path.write_text(json.dumps(metadata), encoding="utf-8")


def new_ticket(case):
    return case.cli("create", "--dir", str(case.project / ".icode_output/.icode_output_4"),
                    "--ticket-id", "unrelated-next", "--requirement", "ordinary task", "--birth", "init")


def test_old_nonstage_metric_damage_does_not_enable_or_block_an_unrelated_ticket(ordinary_project):
    case = ordinary_project
    damage_unrelated_metric(case)
    created = new_ticket(case)
    assert created["ok"] is True


def test_real_stage_history_with_metric_damage_still_blocks_after_locator_directory_loss(ordinary_project):
    case = ordinary_project
    case.enable()
    marker = case.project / ".icode_output/.stage_program"
    marker.rename(marker.with_name("saved-stage-reference"))
    damage_unrelated_metric(case)
    with pytest.raises(case.ctl.ControlError):
        new_ticket(case)


def test_other_ordinary_ticket_corrupt_ledger_reports_unknown_without_mutation(ordinary_project):
    case = ordinary_project
    path = case.first / case.ctl.EVENTS_NAME
    path.write_bytes(path.read_bytes() + b'{broken json}\n')
    before = {p: p.read_bytes() for p in case.project.rglob('*') if p.is_file()}

    view = case.ctl.stage_projection(case.second)

    assert view['state'] == 'unknown'
    assert view['enabled'] is True  # Existing consumers must continue to fail closed.
    assert view['blocked'] is True
    assert view['available_actions'] == []
    assert view['generation'] is None
    assert view['discovery']['reason'] == 'invalid_control_json'
    assert view['discovery']['path'] == str(path)
    assert view['discovery']['control_root'] == str(case.first)
    assert view['discovery']['ticket_id'] == case.ctl.load_metadata(case.first)['ticket_id']
    assert view['discovery']['line'] == 2
    assert view['recovery']['action'] == 'repair_control_ledger'
    assert view['recovery']['path'] == str(path)
    for action in ('status', 'trace', 'check', 'inspect'):
        assert case.ctl.require_stage_action(case.second, action)['state'] == 'unknown'
    with pytest.raises(case.ctl.ControlError) as error:
        case.ctl.require_stage_action(case.second, 'code')
    assert error.value.extra['gate_id'] == 'stage_gate'
    assert error.value.extra['stage']['discovery']['path'] == str(path)
    assert before == {p: p.read_bytes() for p in case.project.rglob('*') if p.is_file()}


def test_discovery_budget_error_identifies_limit_and_control_file(ordinary_project):
    case = ordinary_project
    with pytest.raises(case.ctl.ControlError) as error:
        case.ctl.stage_module().discover_stage_history(case.project, limits={'max_file_bytes': 1})
    diagnostic = error.value.extra['discovery']
    assert diagnostic['reason'] == 'discovery_budget_exhausted'
    assert diagnostic['limit'] == 'max_file_bytes'
    assert diagnostic['path'].endswith(case.ctl.METADATA_NAME)
    assert diagnostic['limits']['max_file_bytes'] == 1
    assert error.value.extra['recovery']['action'] == 'restore_stage_locator'


def test_bound_ticket_missing_locator_is_unknown_with_restore_path(ordinary_project):
    case = ordinary_project
    case.enable()
    locator = case.ctl.stage_module().locator_path(case.project)
    locator.unlink()
    view = case.ctl.stage_projection(case.second)
    assert view['state'] == 'unknown'
    assert view['discovery']['reason'] == 'missing_stage_locator'
    assert view['discovery']['path'] == str(locator)
    assert view['recovery']['action'] == 'restore_stage_locator'
    assert case.ctl.require_stage_action(case.second, 'status')['state'] == 'unknown'
    with pytest.raises(case.ctl.ControlError):
        case.ctl.require_stage_action(case.second, 'code')


def test_discovery_unsafe_debug_path_identifies_the_link(ordinary_project):
    case = ordinary_project
    link = case.project / '.icode_output/.debug'
    try:
        link.symlink_to(case.project / 'missing-debug-target', target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('host cannot create directory symlinks')
    with pytest.raises(case.ctl.ControlError) as error:
        case.ctl.stage_module().discover_stage_history(case.project)
    assert error.value.extra['discovery']['reason'] == 'unsafe_control_path'
    assert error.value.extra['discovery']['path'] == str(link)
    assert error.value.extra['recovery']['action'] == 'inspect_control_path'


def test_bound_ticket_damaged_coordinator_json_identifies_ledger(ordinary_project):
    case = ordinary_project
    case.enable()
    path = case.coord / case.ctl.EVENTS_NAME
    path.write_bytes(path.read_bytes() + b'{broken json}\n')
    view = case.ctl.stage_projection(case.second)
    assert view['state'] == 'unknown'
    assert view['discovery']['path'] == str(path)
    assert view['discovery']['control_root'] == str(case.coord)
    assert str(path) in view['reasons'][0]


def test_bound_ticket_invalid_coordinator_metadata_identifies_metadata(ordinary_project):
    case = ordinary_project
    case.enable()
    path = case.coord / case.ctl.METADATA_NAME
    meta = json.loads(path.read_text(encoding='utf-8'))
    meta['status'] = 'invalid-status'
    path.write_text(json.dumps(meta), encoding='utf-8')
    view = case.ctl.stage_projection(case.second)
    assert view['state'] == 'unknown'
    assert view['discovery']['path'] == str(path)
    assert view['discovery']['control_root'] == str(case.coord)


@pytest.mark.parametrize('damaged_ticket', ['coordinator', 'child'])
@pytest.mark.parametrize('metadata', [[], None, 'non-object metadata'])
def test_non_object_stage_metadata_is_unknown_and_read_only(ordinary_project, damaged_ticket, metadata):
    case = ordinary_project
    case.enable()
    directory = case.coord if damaged_ticket == 'coordinator' else case.second
    path = directory / case.ctl.METADATA_NAME
    path.write_text(json.dumps(metadata), encoding='utf-8')
    before = {p: p.read_bytes() for p in case.project.rglob('*') if p.is_file()}

    view = case.ctl.stage_projection(case.second)

    assert view['state'] == 'unknown'
    assert view['blocked'] is True
    assert view['discovery']['reason'] == 'invalid_control_metadata'
    assert view['discovery']['path'] == str(path)
    assert view['discovery']['control_root'] == str(directory)
    assert view['recovery']['action'] == 'repair_control_metadata'
    assert view['recovery']['path'] == str(path)
    for action in ('status', 'trace', 'check', 'inspect'):
        assert case.ctl.require_stage_action(case.second, action)['state'] == 'unknown'
    with pytest.raises(case.ctl.ControlError) as error:
        case.ctl.require_stage_action(case.second, 'code')
    assert error.value.extra['gate_id'] == 'stage_gate'
    assert before == {p: p.read_bytes() for p in case.project.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damaged_ticket,missing_locator', [('coordinator', False), ('child', False), ('child', True)])
@pytest.mark.parametrize('field', ['extensions', 'extensions.verification'])
@pytest.mark.parametrize('value', [['broken'], 'broken'])
def test_non_object_nested_stage_metadata_is_unknown(ordinary_project, damaged_ticket, missing_locator, field, value):
    case = ordinary_project
    case.enable()
    if missing_locator:
        marker = case.ctl.stage_module().locator_path(case.project).parent
        marker.rename(marker.with_name('saved-stage-reference'))
    directory = case.coord if damaged_ticket == 'coordinator' else case.second
    path = directory / case.ctl.METADATA_NAME
    metadata = json.loads(path.read_text(encoding='utf-8'))
    if field == 'extensions':
        metadata['extensions'] = value
    else:
        metadata.setdefault('extensions', {})['verification'] = value
    path.write_text(json.dumps(metadata), encoding='utf-8')
    before = {p: p.read_bytes() for p in case.project.rglob('*') if p.is_file()}

    view = case.ctl.stage_projection(case.second)

    assert view['state'] == 'unknown'
    assert view['blocked'] is True
    assert view['discovery']['reason'] == 'invalid_control_metadata'
    assert field in view['discovery']['message']
    assert view['discovery']['path'] == str(path)
    assert view['discovery']['control_root'] == str(directory)
    assert view['recovery']['action'] == 'repair_control_metadata'
    for action in ('status', 'trace', 'check', 'inspect'):
        assert case.ctl.require_stage_action(case.second, action)['state'] == 'unknown'
    with pytest.raises(case.ctl.ControlError):
        case.ctl.require_stage_action(case.second, 'code')
    assert before == {p: p.read_bytes() for p in case.project.rglob('*') if p.is_file()}


@pytest.mark.parametrize('extensions', ['missing', None, {}, {'verification': None}])
def test_optional_nested_metadata_keeps_ordinary_ticket_compatible(ordinary_project, extensions):
    case = ordinary_project
    path = case.second / case.ctl.METADATA_NAME
    metadata = json.loads(path.read_text(encoding='utf-8'))
    if extensions == 'missing':
        metadata.pop('extensions', None)
    else:
        metadata['extensions'] = extensions
    path.write_text(json.dumps(metadata), encoding='utf-8')
    assert case.ctl.stage_projection(case.second)['state'] == 'not_enabled'
