"""Missing host tools must allow honest fallback without allowing silent skips."""

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("thinking_unavailable", ROOT / "tools/lint_thinking_gate.py")
thinking = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(thinking)
CATALOG = json.loads((ROOT / "mcp/reasoning-gate/gates.json").read_text(encoding="utf-8"))


def trace(step="plan", tier="L2", **changes):
    row = dict(schema_version=1, ticket_id="unavailable-test", step=step,
        tier=tier, default_tier=CATALOG["steps"][step]["default_tier"],
        triggers=["new_global_gate"] if tier == "L3" else [],
        mechanism="sequential-thinking+adversarial" if tier == "L3" else "sequential-thinking",
        attempted=False, result="degraded", degraded_reason="session tool not exposed",
        decision="unavailable_before_call", over_invoked=False, at="2026-11-13T08:00:00Z",
        availability=dict(tool_visible=False, discovery_ref="session:tool-discovery",
            reason="not_exposed", fallback="structured_text", evidence_ref="01_plan.md#analysis"))
    row.update(changes)
    return row


def issues(row):
    return thinking.validate_row(row, CATALOG["steps"][row["step"]], CATALOG, 1, True)[0]


@pytest.mark.parametrize("step", ["plan", "review", "code", "patch", "log", "deepcheck", "audit"])
@pytest.mark.parametrize("tier", ["L2", "L3"])
def test_unexposed_tool_allows_evidenced_fallback(step, tier):
    assert not issues(trace(step, tier))


@pytest.mark.parametrize("changes", [
    {"attempted": True}, {"attempted": "false"}, {"result": "success"},
    {"degraded_reason": None}, {"degraded_reason": " "}, {"decision": None},
    {"availability": None}, {"availability": {}}, {"availability": []},
])
def test_unavailable_rejects_contradictory_or_missing_evidence(changes):
    assert issues(trace(**changes))


@pytest.mark.parametrize("field,value", [
    ("tool_visible", True), ("tool_visible", 0), ("reason", "timeout"),
    ("discovery_ref", ""), ("discovery_ref", 123), ("fallback", " "), ("evidence_ref", None),
])
def test_availability_requires_discovery_and_fallback_references(field, value):
    row = trace()
    row["availability"][field] = value
    assert issues(row)


def test_l3_still_requires_adversarial_mechanism():
    assert issues(trace(tier="L3", mechanism="sequential-thinking"))


@pytest.mark.parametrize("result", ["success", "degraded"])
def test_existing_real_call_trace_remains_compatible(result):
    row = trace(attempted=True, result=result, degraded_reason="timeout" if result == "degraded" else None)
    row.pop("decision")
    row.pop("availability")
    assert not issues(row)


def test_unavailable_is_not_a_low_tier_escape():
    assert issues(trace(step="merge", tier="L1", mechanism="decision_record"))


def test_active_step_decision_lists_include_pre_call_unavailability():
    for path in (ROOT / "steps").glob("*.md"):
        assert not re.search(r"called/cache_hit/degraded_after_attempt(?!/unavailable_before_call)",
                             path.read_text(encoding="utf-8")), path


def test_strict_cli_and_control_plane_accept_honest_degradation(tmp_path):
    metadata = dict(ticket_id="unavailable-test", thinking_gate_schema_version=1,
                    completed_steps=[], status="init_in_progress")
    (tmp_path / ".ico_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    # A final fallback resolves the initial pending record on a fresh plan run.
    rows = [trace(result="pending", decision=None), trace()]
    (tmp_path / ".thinking_gate_trace.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    command = [sys.executable, "-X", "utf8", str(ROOT / "tools/lint_thinking_gate.py"), str(tmp_path),
               "--step", "plan", "--strict", "--json"]
    proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["steps"][0]["status"] == "fulfilled"
    assert report["steps"][0]["trace_row"]["result"] == "degraded"
    control_spec = importlib.util.spec_from_file_location("unavailable_control", ROOT / "tools/icode_control.py")
    control = importlib.util.module_from_spec(control_spec)
    control_spec.loader.exec_module(control)
    checks = control.run_gate_linters(tmp_path, to_status="plan_done")
    check = next(row for row in checks if row["gate_id"] == "thinking_gate")
    assert check["ok"], check
