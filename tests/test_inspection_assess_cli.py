"""Assessment uses real attempts, writer receipts and replay protection."""
import copy
import json
import pytest

from test_inspection_integration import cli, inspected
from test_inspection_checks import CLOCK_SOURCE, assessment


def prepare(root, out, source=CLOCK_SOURCE):
    (root / "src/a.c").write_text(source)
    cli(out, "inspection", "--step", "code", "--phase", "prepare", "--attempt", "inspect-a", "--request", "prep")
    path = out / "code_worklist.json"
    report = json.loads(path.read_text())
    for unit in report["units"]:
        for item in unit["files"]:
            (root / item["path"]).read_text()
            cli(out, "inspection", "--step", "code", "--phase", "read", "--attempt", "inspect-a",
                "--read-phase", "code_review", "--path", item["path"])
    return path, json.loads(path.read_text())


def assess(out, item, payload, request="assess-1", ok=True):
    return cli(out, "inspection", "--step", "code", "--phase", "assess", "--attempt", "inspect-a",
        "--read-phase", "code_review", "--check-id", item["check_id"], "--assessment-json", json.dumps(payload),
        "--request", request, ok=ok)


def test_prepare_writer_marks_attempt_even_without_trigger(inspected):
    root, out = inspected
    _, report = prepare(root, out, "int a(void) { return 1; }\n")
    assert report.get("checks_version") == 1
    events = [json.loads(line) for line in (out / ".ico_events.jsonl").read_text().splitlines()]
    prepares = [e for e in events if e.get("request_id") == "prep"]
    assert len(prepares) == 1
    assert prepares[0]["payload"].get("inspection_checks_version") == 1


def test_assessment_cli_receipt_replay_and_payload_conflict(inspected):
    root, out = inspected
    path, report = prepare(root, out)
    item = report.get("checks", [None])[0]
    assert item is not None, "timestamp check must be derived before assessment"
    payload = assessment(report, item, root)
    first = assess(out, item, payload)
    assert cli(out, "inspection", "--step", "code", "--phase", "check", "--attempt", "inspect-a")["ok"]
    before = path.read_bytes()
    replay = assess(out, item, payload)
    assert replay["already_applied"] and replay["event_id"] == first["event_id"]
    assert path.read_bytes() == before
    changed = copy.deepcopy(payload)
    changed["cells"]["sentinel"]["reason"] = "Different review conclusion for the same request."
    failed = assess(out, item, changed, ok=False)
    assert failed["gate_id"] == "idempotency_conflict"
    assert path.read_bytes() == before


@pytest.mark.parametrize("source", [CLOCK_SOURCE, "int a(void) { return 1; }\n"])
def test_new_writer_receipt_prevents_field_deletion_downgrade(inspected, source):
    root, out = inspected
    path, report = prepare(root, out, source)
    report.pop("checks_version", None)
    report.pop("checks", None)
    path.write_text(json.dumps(report))
    result = cli(out, "inspection", "--step", "code", "--phase", "check", "--attempt", "inspect-a", ok=False)
    assert any("checks" in issue or "declaration" in issue for issue in result["violations"])
    failed = cli(out, "inspection", "--step", "code", "--phase", "prepare", "--attempt", "inspect-a", ok=False)
    assert failed["gate_id"] == "inspection_worklist"


def test_read_completion_does_not_assess_and_invalid_assessment_preserves_file(inspected):
    root, out = inspected
    path, report = prepare(root, out)
    assert report.get("checks"), "Read must retain pending explicit assessments"
    assert not report["checks"][0]["results"]
    cli(out, "inspection", "--step", "code", "--phase", "check", "--attempt", "inspect-a", ok=False)
    before = path.read_bytes()
    payload = assessment(report, report["checks"][0], root)
    payload["cells"].pop("sentinel")
    failed = assess(out, report["checks"][0], payload, ok=False)
    assert failed["gate_id"] == "inspection_worklist"
    assert path.read_bytes() == before


def test_declaration_hash_binds_special_identity_but_not_results(inspected):
    import importlib.util
    from test_inspection_integration import CONTROL
    spec = importlib.util.spec_from_file_location("inspection_control_test", CONTROL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root, out = inspected
    _, report = prepare(root, out)
    assert report.get("checks"), "special identity is required for declaration binding"
    before = module.inspection_declaration_hash(report)
    report["checks"][0]["results"]["code_review"] = assessment(report, report["checks"][0], root)
    assert module.inspection_declaration_hash(report) == before
    report["checks"][0]["required_items"].pop()
    assert module.inspection_declaration_hash(report) != before


def test_malformed_top_level_worklist_returns_structured_rejection(inspected):
    _, out = inspected
    cli(out, "inspection", "--step", "code", "--phase", "prepare", "--attempt", "inspect-a")
    (out / "code_worklist.json").write_text("[]\n")
    result = cli(out, "inspection", "--step", "code", "--phase", "check", "--attempt", "inspect-a", ok=False)
    assert result["violations"]


def test_read_only_helper_uses_real_writer_attempt_not_report_legacy_claim(inspected):
    import importlib.util
    from test_inspection_integration import CONTROL
    spec = importlib.util.spec_from_file_location("inspection_control_marker_test", CONTROL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root, out = inspected
    path, report = prepare(root, out, "int a(void) { return 1; }\n")
    report.pop("checks_version")
    report.pop("checks")
    report["attempt"] = "invented-old-attempt"
    path.write_text(json.dumps(report))
    assert module.inspection_worklist_issues(out, module.load_metadata(out), "code")
