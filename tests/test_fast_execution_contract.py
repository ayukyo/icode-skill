"""Guard the responsibilities of the source-bound FAST instruction excerpts.

These are contract checks, not a claim of natural-language semantic equivalence.
Specification review remains necessary when changing any mapped responsibility.
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PHASES = {
    "plan": "01_plan.md",
    "review": "02_review.md",
    "merge": "03_merge.md",
    "code": "04_code.md",
    "deepcheck": "05_deepcheck.md",
    "audit": "06_audit.md",
}

# Stable responsibilities and original normative sections; the runtime catalog
# binds their complete document versions, not just these test-visible strings.
REQUIREMENT_SOURCES = {
    "plan": (("FAST-PLAN-SCOPE", "plan-execution"), ("FAST-PLAN-DESIGN", "plan-design"), ("FAST-PLAN-GATES", "plan-gates")),
    "review": (("FAST-REVIEW-INDEPENDENT", "review-first-round"), ("FAST-REVIEW-SEVEN", "review-first-round"), ("FAST-REVIEW-RECEIPTS", "review-receipts")),
    "merge": (("FAST-MERGE-COMPLETE", "merge-finalization"), ("FAST-MERGE-GATES", "merge-validation")),
    "code": (("FAST-CODE-TDD", "code-tdd"), ("FAST-CODE-IMPLEMENT", "code-implementation"), ("FAST-CODE-REVIEW-FIX", "code-verification")),
    "deepcheck": (("FAST-REVERSE-INDEPENDENT", "deepcheck-reverse"), ("FAST-REVERSE-EVIDENCE", "deepcheck-coverage"), ("FAST-REVERSE-COVERAGE", "deepcheck-coverage")),
    "audit": (("FAST-AUDIT-INDEPENDENT", "audit-dimensions"), ("FAST-AUDIT-CLOSE", "audit-close"), ("FAST-AUDIT-RECEIPTS", "audit-completion")),
}


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def excerpt(phase):
    text = read("steps/fast.md")
    begin = f"<!-- ICODE:FAST:{phase}:BEGIN -->"
    end = f"<!-- ICODE:FAST:{phase}:END -->"
    assert text.count(begin) == text.count(end) == 1
    result = text.split(begin, 1)[1].split(end, 1)[0]
    assert f'<a id="fast-{phase}"></a>' in result
    return result


@pytest.mark.parametrize("phase,filename", PHASES.items())
def test_phase_has_one_canonical_contract_and_short_navigation(phase, filename):
    assert excerpt(phase).strip()
    main = read(f"steps/{filename}")
    navigation = main.split("## fast 必要执行", 1)[1].split("\n## ", 1)[0]
    assert f"fast.md#fast-{phase}" in navigation
    assert len(navigation.encode("utf-8")) < 1200


def test_responsibility_ids_bind_unique_original_normative_sections():
    all_ids = []
    for phase, requirements in REQUIREMENT_SOURCES.items():
        original = read(f"steps/{PHASES[phase]}")
        for requirement_id, anchor in requirements:
            assert excerpt(phase).count(f"**{requirement_id}**") == 1
            assert original.count(f'<a id="{anchor}"></a>') == 1
            all_ids.append(requirement_id)
    assert len(all_ids) == len(set(all_ids))


def test_plan_retains_scope_design_and_test_contract():
    text = excerpt("plan")
    for term in ("10", "§4.5", "ADR", "scope_contract", "A/B/C", "红线", "调用链", "异常", "验收", "RED", "GREEN"):
        assert term in text


def test_review_retains_independent_first_round_and_seven_dimensions():
    text = excerpt("review")
    for term in ("先", "独立", "原始需求", "再", "01_plan.md", "逻辑合理性", "流程完整性", "场景覆盖度", "风险遗漏", "落地可行性", "现有实现对照", "必要性", "review_round_1.json", "review_manifest.json", "origin", "artifact"):
        assert term in text


def test_merge_retains_complete_approved_plan_and_frozen_tests():
    text = excerpt("merge")
    for term in ("01_plan.md", "全文", "03_plan_final.md", "10", "实现偏差备忘", "TDD", "RED", "GREEN", "check-outputs", "lint_workflow_contract.py"):
        assert term in text


def test_code_retains_tdd_four_dimension_fix_and_real_read():
    text = excerpt("code")
    for term in ("RED→GREEN", "expected_assertion", "回归", "Code Review Fix", "根因闭环", "逻辑", "竞态死锁", "日志", "code_review", "code_worklist.json", "04_code_review_fix.md", "--paths-json"):
        assert term in text


def test_reverse_and_audit_have_separate_read_assessment_and_verdicts():
    reverse = excerpt("deepcheck")
    audit = excerpt("audit")
    for text, phase in ((reverse, "reverse"), (audit, "audit")):
        for term in ("独立", "实际 Read", "--paths-json", phase, "assessment", "coverage", "artifact", "degraded"):
            assert term in text
    for term in ("先", "代码", "逆推", "再", "03_plan_final.md", "patch", "调用链", "TDD"):
        assert term in reverse
    for term in ("追溯矩阵", "原始需求", "新裁决", "delivery_verdict", "check-outputs"):
        assert term in audit


def test_each_excerpt_loads_shared_gates_and_preserves_step_receipts():
    for phase in PHASES:
        text = excerpt(phase)
        assert "#fast-shared-contract" in text
        assert "required_checks" in text
        assert "finish" in text and "transition" in text
    shared = read("steps/fast.md").split('<a id="fast-shared-contract"></a>', 1)[1]
    for source in ("thinking_core.md", "execution_model.md", "inspection_worklist.md", "inspection_evidence.md", "serial_evidence_stages.md", "evidence_and_verification.md", "skill_routing.md", "worktree_isolation.md"):
        assert source in shared
    for term in ("semantic_decisions", "impact_contract", "acceptance_contract", "requirement_deltas", "身份", "生命周期", "真实环境", "阶段确认", "公共库"):
        assert term in shared


def test_shared_batch_route_is_real_before_register_and_bounded_for_both_modes():
    worklist = read("references/inspection_worklist.md")
    connection = worklist.split("## 正式工单接线", 1)[1].split("## 专项结论", 1)[0]
    assert connection.index("实际") < connection.index("--paths-json")
    assert connection.index("--paths-json") < connection.index("--path ")
    for term in ("full", "start", "fast", "256", "64KiB", "分组", "hash", "漂移", "独立", "--path"):
        assert term in connection
    assert "--paths-json" in read("references/execution_model.md")
    for filename in ("04_code.md", "05_deepcheck.md", "06_audit.md"):
        full = re.sub(r"## fast 必要执行\n.*?(?=\n## )", "", read(f"steps/{filename}"), flags=re.S)
        assert "--paths-json" in full


def test_root_routes_only_validated_compact_at_step_start_with_standard_fallback():
    text = read("SKILL.md")
    for term in ("execution_profile", "source_digest", "source_refs", "compact", "effective_mode", "policy_version", "standard", "unavailable", "legacy", "步骤开始", "失效"):
        assert term in text
    assert len((ROOT / "SKILL.md").read_bytes()) <= 51200


def test_full_review_and_deepcheck_strength_and_reasoning_remain():
    review = read("steps/02_review.md")
    deepcheck = read("steps/05_deepcheck.md")
    for term in ("absolute_cap = max(10, N×2)", "连续 2 轮无新问题", "对抗验证", "3 质疑者"):
        assert term in review
    for term in ("阶段 1 — Reverse", "阶段 2 — Fixed", "阶段 3 — Free", "阶段 4 — Dedup", "15", "独立"):
        assert term in deepcheck
    fast = read("steps/fast.md")
    assert "L2" in fast and "至少 3 步" in fast
    assert "skipped_stage_not_reached" in fast
    # Exactly six extraction boundaries, and no nested/unrecognised phase names.
    assert re.findall(r"<!-- ICODE:FAST:([a-z]+):BEGIN -->", fast) == list(PHASES)
