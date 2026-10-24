"""Post-delivery evidence uses existing ports; no field state or hash bypass."""
import json

import pytest

from test_candidate_inspection_generations import candidate, migrate_old_completed
from test_candidate_freshness import ctl
from test_delivery_report import delivery


@pytest.mark.parametrize("outcome", ["pass", "fail", "inconclusive"])
def test_completed_patch_verify_receipts_preserve_history_and_debt(candidate, outcome):
    migrate_old_completed(candidate)
    contract = {"required": True, "required_layers": ["physical"],
                "required_consumers": ["camera"], "required_scenarios": ["field-fixture"]}
    candidate.call("metadata-update", "--dir", candidate.ticket, "--set-json",
                   json.dumps({"verification_contract": contract}), "--request-id", "field-contract")
    old = ctl.load_metadata(candidate.ticket)
    assert old["status"] == "completed"
    artifact = candidate.ticket / "historical-field-evidence.log"
    artifact.write_bytes(b"immutable historical evidence\n")
    previous_events = (candidate.ticket / ctl.EVENTS_NAME).read_bytes()
    candidate.call("step", "--dir", candidate.ticket, "--step", "patch", "--phase", "start", "--attempt", "field-patch")
    (candidate.repo / "source.py").write_text("value = 2\n", encoding="utf-8", newline="\n")
    # A blocked patch attempt is still accounted for. Validation can record
    # observations independently; it must not promote this patch to accepted.
    candidate.call("step", "--dir", candidate.ticket, "--step", "patch", "--phase", "finish",
                   "--attempt", "field-patch", "--outcome", "blocked", "--evidence", "fixture change needs review")
    log = candidate.ticket / "field-fixture-new-round.log"
    log.write_bytes(b"software fixture only; no device execution\n")
    candidate_id = ctl.candidate_snapshot(candidate.ticket, ctl.load_metadata(candidate.ticket))["candidate_id"]
    args = ["record-verification", "--dir", candidate.ticket, "--kind", "device_test", "--outcome", outcome,
            "--environment", "simulated", "--candidate-id", candidate_id, "--layer", "physical",
            "--consumer", "camera", "--scenario", "field-fixture", "--device", "fixture-only",
            "--artifact-identity", "fixture-artifact", "--window", "fixture-window",
            "--baseline", "fixture source", "--evidence", log.name, "--request-id", "field-record"]
    candidate.call(*args)
    registered = (candidate.ticket / ctl.EVENTS_NAME).read_bytes()
    replay = candidate.call(*args)
    assert replay["already_applied"]
    assert (candidate.ticket / ctl.EVENTS_NAME).read_bytes() == registered
    assert registered.startswith(previous_events) and artifact.read_bytes() == b"immutable historical evidence\n"
    current = ctl.load_metadata(candidate.ticket)
    assert current["status"] == "completed" and current["completed_steps"] == old["completed_steps"]
    assert current.get("delivery_verdict") == old.get("delivery_verdict")
    report = delivery().build_delivery_report(candidate.ticket)
    assert report["verification"]["debt"]["pending_units"] == 1  # simulated never proves physical acceptance
    assert not report["ok"]
    assert current["verification_runs"][-1]["outcome"] == outcome
    # Source drift cannot reuse an old observation to certify the current source.
    (candidate.repo / "source.py").write_text("value = 3\n", encoding="utf-8", newline="\n")
    assert not ctl.verification_applicability(candidate.ticket)["ok"]
    assert (candidate.ticket / ctl.EVENTS_NAME).read_bytes() == registered
