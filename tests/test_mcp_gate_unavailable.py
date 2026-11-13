"""Every existing MCP coverage gate accepts evidenced pre-call unavailability."""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("coverage_unavailable", ROOT / "tools/lint_mcp_coverage.py")
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)
CATALOG = json.loads((ROOT / "mcp/cheap-research/gates.json").read_text(encoding="utf-8"))

# Real catalog conditions, including registered/provider readiness versus this
# session's tool exposure. Tool discovery and provider configuration differ.
EVIDENCE = {
    "log.comments_extract": dict(tb_comments_count=8, threshold=8, cheap_available=True),
    "log.long_log_summary": dict(candidate_text_bytes=8192, threshold=8192, cheap_available=True),
    "review.dedup": dict(affected_repo_roots=["/fixture"], function_count=50, threshold=50,
                         rg_available=True, cheap_available=True),
    "review.result_summary": dict(result_source="02_review.md", review_rounds=1),
    "merge.cross_round_summary": dict(review_rounds=2, threshold=2),
    "deepcheck.fixed_scan": dict(mode="full", phase="fixed", function_points_count=1),
    "deepcheck.dedup": dict(mode="full", phase="dedup", function_count=50, threshold=50),
    "audit.repo_facts": dict(affected_repo_roots=["/fixture"]),
    "audit.plan_diff": dict(plan_source="03_plan_final.md", code_sources=["source.py"]),
    "patch.context_summary": dict(cross_session=True, candidate_text_bytes=8192, threshold=8192),
    "patch.listen_log_summary": dict(listen_mode=True, incremental_bytes=8192, threshold=8192),
    "verify.listen_log_summary": dict(listen_mode=True, incremental_bytes=8192, threshold=8192),
}


def report(tmp_path, gate, *, missing_provider=False, missing_evidence=False):
    evidence = dict(EVIDENCE[gate["id"]])
    if missing_provider:
        evidence["cheap_available"] = False
    row = dict(schema_version=2, ticket_id="coverage-test", step=gate["step"],
        gate_id=gate["id"], tool=gate["tool"], eligible=not missing_provider, evidence=evidence,
        decision="skipped_not_eligible" if missing_provider else "unavailable_before_call",
        attempted=False, result="not_applicable" if missing_provider else "unavailable",
        at="2026-11-13T08:00:00Z")
    if not missing_evidence:
        row["availability"] = dict(tool_visible=False, reason="not_exposed",
            discovery_ref="session:tools", fallback="main_agent", evidence_ref="fallback-report.md")
    metadata = dict(ticket_id="coverage-test", mcp_gate_schema_version=2, mode="full")
    return coverage.build_report(tmp_path, gate["step"], False, metadata, [row],
                                 {**CATALOG, "gates": [gate]}, [])


@pytest.mark.parametrize("gate", CATALOG["gates"], ids=lambda gate: gate["id"])
def test_unavailable_gate_keeps_evidenced_fallback(tmp_path, gate):
    result = report(tmp_path, gate)
    assert coverage.report_is_pass(result, False, True), result
    assert result["unavailable_before_call"] == 1
    assert result["called"] == 0


@pytest.mark.parametrize("gate", CATALOG["gates"], ids=lambda gate: gate["id"])
def test_unavailable_gate_cannot_omit_evidence(tmp_path, gate):
    assert not coverage.report_is_pass(report(tmp_path, gate, missing_evidence=True), False, True)


@pytest.mark.parametrize("gate", CATALOG["gates"][:3], ids=lambda gate: gate["id"])
def test_catalog_provider_condition_remains_compatible(tmp_path, gate):
    result = report(tmp_path, gate, missing_provider=True)
    assert coverage.report_is_pass(result, False, True), result
    assert result["skipped_not_eligible"] == 1
    assert result["called"] == 0
