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
