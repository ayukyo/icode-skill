"""Drive Start/Fast production gates with local evidence and no MCP calls."""
import ast
import json
import uuid

import pytest

import test_candidate_freshness as fixtures

ctl = fixtures.ctl


@pytest.mark.parametrize("mode", ["full", "fast"])
def test_six_phase_workflow_without_mcp(mode, monkeypatch, tmp_path):
    fixture = fixtures.CandidateTests()
    fixture.setUp()
    try:
        monkeypatch.delenv("ICODE_CONTROL_TEST_MODE", raising=False)
        ticket_id = "no-mcp-" + uuid.uuid4().hex
        out, repo = fixture.ticket, fixture.repo
        model = ctl.load_execution_model()

        def call(*args, **kwargs):
            return fixture.call(*args, "--dir", out, **kwargs)

        def write(name, content):
            path = out / name
            path.write_text(content, encoding="utf-8", newline="\n")
            return path

        def append(name, row):
            with (out / name).open("a", encoding="utf-8") as f:
                f.write(json.dumps(dict(row, ticket_id=ticket_id, at=ctl.now_iso())) + "\n")

        def unavailable(ref):
            return dict(decision="unavailable_before_call", attempted=False,
                        availability=dict(tool_visible=False, reason="not_exposed",
                        discovery_ref="isolated CLI fixture has no MCP tools", fallback="local source review",
                        evidence_ref=ref))

        def thinking(step, ref):
            if step == "merge":
                row = dict(tier="L1", default_tier="L1", mechanism="decision_record",
                           attempted=False, result="success", degraded_reason=None)
            else:
                row = dict(tier="L2", default_tier="L2", mechanism="sequential-thinking",
                           result="degraded", degraded_reason="MCP is not exposed", **unavailable(ref))
            append(".thinking_gate_trace.jsonl",
                   dict(row, schema_version=1, step=step, triggers=[], over_invoked=False))

        def trace(step, gate, tool, ref, **evidence):
            if ref:
                row = dict(eligible=True, result="unavailable", **unavailable(ref))
            else:
                decision = ("skipped_stage_not_reached" if evidence.get("phase") == "reverse"
                            else "skipped_not_eligible")
                row = dict(eligible=False, attempted=False, result="not_applicable", decision=decision)
            append(".mcp_gate_trace.jsonl", dict(row, schema_version=2, contract_version=2,
                   step=step, gate_id=f"{step}.{gate}", tool=tool, attempt=step + "-1",
                   evidence=evidence))

        def phase(step, action):
            boundary = ("--boundary", action) if action in {"before_write", "after_wait", "before_transition"} else ()
            return call("step", "--step", step, "--attempt", step + "-1",
                        "--phase", "check" if boundary else action, *boundary)

        def finish(step, *outputs):
            for name in outputs:
                call("artifact", "--step", step, "--attempt", step + "-1", "--path", name)
            for boundary in model["step_contracts"][step]["required_checks"]:
                phase(step, boundary)
            call("step", "--step", step, "--attempt", step + "-1", "--phase", "finish",
                 "--outcome", "success", "--evidence", outputs[0])

        call("create", "--ticket-id", ticket_id, "--birth", "plan",
             "--requirement", "Change the fixture value", "--metadata-json",
             json.dumps(dict(mode=mode, code_files=["source.py"], semantic_decisions=[], requirement_deltas=[])))
        phase("plan", "start")
        phase("plan", "before_write")
        plan = write("01_plan.md", "# Plan\nThe source contains a single assignment. Update its value.\n")
        if mode == "fast":
            risk = ctl.load_json(ctl.GATES_JSON, "gates")["fast_risk_upgrade"]
            scope = dict(summary="One assignment", source_step="plan", at=ctl.now_iso())
            profile = dict(policy_version=2, requested_mode="fast", effective_mode="fast", override=False,
                           triggers=[], risk_flags=dict.fromkeys([*risk["risk_flags"],
                                       *risk["policy_v2"]["full_flags"]], False),
                           classification=dict(scope="bounded", scope_digest=ctl.canonical_digest(scope),
                               evidence_refs=[dict(scope="ticket", path=plan.name, sha256=ctl.file_sha256(plan))]))
            call("metadata-update", "--set-json", json.dumps(dict(scope_contract=scope, risk_profile=profile)))
        thinking("plan", plan.name)
        finish("plan", plan.name)
        call("transition", "--to", "plan_done")
        call("transition", "--to", "review_in_progress")

        phase("review", "start")
        phase("review", "before_write")
        review = write("02_review.md", "# Review\nRead the assignment and plan directly.\n")
        first = write("review_round_1.json", json.dumps(dict(new_issues=[], refuted_issues=[],
                      pending_verification=[], has_new_issues=False)))
        rounds = [dict(round=1, origin_attempt="review-1", new_issues=0, refuted_issues=0,
                       pending_verification=0, detail_path=first.name, detail_sha256=ctl.file_sha256(first))]
        if mode == "full":
            rounds.extend(dict(round=n, origin_attempt="review-1", new_issues=0, refuted_issues=0,
                               pending_verification=0, detail_path=None, detail_sha256=None) for n in (2, 3))
        manifest = write("review_manifest.json", json.dumps(dict(
            schema_version=1, ticket_id=ticket_id, review_run="review-1", rounds=rounds)))
        tree = ast.parse((repo / "source.py").read_text(encoding="utf-8"))
        assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))
        cheap = ctl.load_json(ctl.GATE_CONTRACT_CATALOG, "cheap research")["constants"]
        trace("review", "dedup", "extract", None, affected_repo_roots=[str(repo)], function_count=0,
              threshold=cheap["dedup_min_functions"], rg_available=False, cheap_available=False,
              enumeration_verified=True)
        trace("review", "result_summary", "summarize", review.name, result_source=review.name,
              review_rounds=len(rounds))
        thinking("review", review.name)
        finish("review", first.name, manifest.name, review.name)
        call("transition", "--to", "review_done")

        phase("merge", "start")
        final = write("03_plan_final.md", "fixture plan\n")
        trace("merge", "cross_round_summary", "summarize", final.name if mode == "full" else None,
              review_rounds=len(rounds), threshold=cheap["merge_min_rounds"])
        thinking("merge", final.name)
        finish("merge", final.name)
        call("transition", "--to", "plan_finalized")
        call("transition", "--to", "code_in_progress")

        (repo / "source.py").write_text("value = 2\n", encoding="utf-8")
        thinking("code", "04_code_review_fix.md")
        fixture.finish_review("code")
        call("transition", "--to", "code_done")
        call("transition", "--to", "deepcheck_in_progress")
        thinking("deepcheck", "05_deepcheck.md")
        if mode == "fast":
            trace("deepcheck", "fixed_scan", "scan_patterns", None, mode=mode, phase="reverse")
            trace("deepcheck", "dedup", "extract", None, mode=mode, phase="reverse")
        else:
            trace("deepcheck", "fixed_scan", "scan_patterns", "05_deepcheck.md",
                  mode=mode, phase="fixed", function_points_count=1)
            trace("deepcheck", "dedup", "extract", None, mode=mode, phase="dedup", function_count=0,
                  threshold=cheap["dedup_min_functions"])
        fixture.finish_review("deepcheck", write_dedup_trace=False)
        call("transition", "--to", "deepcheck_done")
        thinking("audit", "06_audit.md")
        trace("audit", "repo_facts", "propose_repo_facts", "06_audit.md", affected_repo_roots=[str(repo)])
        trace("audit", "plan_diff", "diff_summary", "06_audit.md",
              plan_source=final.name, code_sources=["source.py"])
        fixture.finish_review("audit")
        if mode == "full":
            trace("review", "dedup", "extract", review.name, affected_repo_roots=[str(repo)],
                  function_count=0, threshold=cheap["dedup_min_functions"],
                  rg_available=False, cheap_available=False, enumeration_unverified=True)
            blocked = call("transition", "--to", "completed", "--delivery-verdict", "verified", ok=False)
            assert blocked["gate_id"] == "gate_enumeration_debt"
            assert ctl.load_metadata(out)["status"] == "deepcheck_done"
            trace("review", "dedup", "extract", None, affected_repo_roots=[str(repo)], function_count=0,
                  threshold=cheap["dedup_min_functions"], rg_available=False, cheap_available=False,
                  enumeration_verified=True, repo_review_scopes={str(repo): ["."]})
        result = call("transition", "--to", "completed", "--delivery-verdict", "verified")
        assert result["enumeration_debt"] == 0
        assert ctl.load_metadata(out)["completed_steps"] == list("123456")
        assert ctl.delivery_freshness(out)["ok"]
        call("validate", "--index", tmp_path / "index.json")
    finally:
        fixture.doCleanups()
