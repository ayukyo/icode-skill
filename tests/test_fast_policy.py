"""Versioned fast routing must preserve hard gates and legacy tickets."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "mcp/workflow-gate/gates.json").read_text(encoding="utf-8"))
OLD_FLAGS = CATALOG["constants"]["fast_risk_triggers"]
HARD_FLAGS = ["new_architecture", "unbounded_impact", "breaking_interface_change",
              "irreversible_migration", "independent_adversarial_required"]


def policy():
    path = ROOT / "tools/fast_policy.py"
    assert path.is_file(), "missing versioned fast routing API"
    spec = importlib.util.spec_from_file_location("fast_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bounded():
    scope = {"summary": "Known caller/provider and acceptance boundary", "at": "2026-11-13T12:00:00Z",
             "source_step": "01_plan"}
    digest = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode()).hexdigest()
    return dict(mode="fast", scope_contract=scope, semantic_decisions=[], requirement_deltas=[],
                risk_profile=dict(policy_version=2, requested_mode="fast", effective_mode="fast",
                    override=False, triggers=[], risk_flags={flag: False for flag in OLD_FLAGS + HARD_FLAGS},
                    classification=dict(scope="bounded", scope_digest=digest,
                        evidence_refs=[dict(scope="ticket", path="01_plan.md", sha256="a" * 64)])))


@pytest.mark.parametrize("flag,check", [("cross_component", "caller_provider_review"),
    ("async_observer_or_cache", "lifecycle_acceptance"),
    ("real_env_verification", "real_environment_verification")])
def test_bounded_risk_keeps_fast_and_required_check(flag, check):
    meta = bounded()
    meta["risk_profile"]["risk_flags"][flag] = True
    result = policy().assess_fast_risk(meta, CATALOG)
    assert result["route"] == "fast"
    assert check in result["required_checks"]


def test_new_api_is_pure_and_reason_codes_are_stable():
    meta = bounded()
    before = copy.deepcopy(meta)
    assert policy().assess_fast_risk(meta, CATALOG) == {
        "route": "fast", "reasons": ["bounded_scope"], "required_checks": []}
    assert meta == before


@pytest.mark.parametrize("flag", HARD_FLAGS)
def test_hard_full_risks_ignore_override(flag):
    meta = bounded()
    meta["risk_profile"]["risk_flags"][flag] = True
    meta["risk_profile"]["override"] = True
    result = policy().assess_fast_risk(meta, CATALOG)
    assert result["route"] == "full"
    assert flag in result["reasons"]


def test_unresolved_semantics_block_even_override():
    meta = bounded()
    meta["risk_profile"]["override"] = True
    meta["semantic_decisions"] = [{"status": "pending"}]
    assert policy().assess_fast_risk(meta, CATALOG)["route"] == "blocked"


def test_major_delta_requires_replan_without_automatic_full():
    meta = bounded()
    meta["requirement_deltas"] = [{"severity": "major", "needs_replan": True, "status": "open"}]
    assert policy().assess_fast_risk(meta, CATALOG)["route"] == "replan"


@pytest.mark.parametrize("field,value", [("mode", "full"), ("effective_mode", "full")])
def test_existing_full_is_never_downgraded(field, value):
    meta = bounded()
    (meta if field == "mode" else meta["risk_profile"])[field] = value
    assert policy().effective_mode(meta) == "full"
    assert policy().assess_fast_risk(meta, CATALOG)["route"] == "full"


@pytest.mark.parametrize("version", [3, "2", True, None])
def test_unknown_policy_versions_are_blocked(version):
    meta = bounded()
    meta["risk_profile"]["policy_version"] = version
    assert policy().assess_fast_risk(meta, CATALOG)["route"] == "blocked"


@pytest.mark.parametrize("mutation", [
    lambda m: m["risk_profile"].pop("classification"),
    lambda m: m["risk_profile"]["classification"].update(evidence_refs=[]),
    lambda m: m["risk_profile"]["classification"].update(scope_digest="b" * 64),
    lambda m: m["risk_profile"]["classification"].update(scope="放心"),
    lambda m: m["risk_profile"]["classification"]["evidence_refs"][0].update(path="../escape"),
    lambda m: m["risk_profile"]["classification"]["evidence_refs"][0].update(sha256="claimed"),
    lambda m: m["risk_profile"]["classification"]["evidence_refs"][0].pop("scope"),
    lambda m: m["risk_profile"]["risk_flags"].update(cross_component="false"),
    lambda m: m["risk_profile"]["risk_flags"].pop("new_architecture"),
    lambda m: m["risk_profile"]["risk_flags"].update(unknown=True),
    lambda m: m["risk_profile"].update(triggers=["unknown"]),
    lambda m: m.update(semantic_decisions={}),
    lambda m: m.update(requirement_deltas="none"),
])
def test_invalid_or_uninvestigated_classification_is_blocked(mutation):
    meta = bounded()
    mutation(meta)
    assert policy().assess_fast_risk(meta, CATALOG)["route"] == "blocked"


@pytest.mark.parametrize("version", ["absent", 1])
@pytest.mark.parametrize("override,expected", [(False, "full"), (True, "fast")])
def test_legacy_nine_flags_keep_original_override_semantics(version, override, expected):
    meta = dict(mode="fast", risk_profile=dict(effective_mode="fast", override=override,
        risk_flags={"cross_component": True}, triggers=[]))
    if version != "absent":
        meta["risk_profile"]["policy_version"] = version
    assert policy().assess_fast_risk(meta, CATALOG)["route"] == expected


def test_legacy_full_and_no_profile_remain_readable():
    assert policy().assess_fast_risk({"mode": "full"}, CATALOG)["route"] == "full"
    assert policy().assess_fast_risk({"mode": "fast"}, CATALOG)["route"] == "fast"


def test_catalog_controls_required_checks():
    api = policy()
    meta = bounded()
    meta["risk_profile"]["risk_flags"]["cross_component"] = True
    catalog = copy.deepcopy(CATALOG)
    catalog["fast_risk_upgrade"]["policy_v2"]["required_checks"]["cross_component"] = ["custom_check"]
    assert api.assess_fast_risk(meta, catalog)["required_checks"] == ["custom_check"]


def linter():
    spec = importlib.util.spec_from_file_location("lint_workflow_contract", ROOT / "tools/lint_workflow_contract.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_linter_accepts_bounded_policy2_without_override():
    meta = bounded()
    meta["risk_profile"]["risk_flags"]["cross_component"] = True
    assert linter().validate_fast_risk(meta, CATALOG) == []


def test_linter_blocks_hard_full_without_recorded_upgrade():
    meta = bounded()
    meta["risk_profile"]["override"] = True
    meta["risk_profile"]["risk_flags"]["new_architecture"] = True
    assert linter().validate_fast_risk(meta, CATALOG)


@pytest.mark.parametrize("step", ["review", "merge", "code", "patch"])
def test_classification_required_at_policy2_admission_only(tmp_path, step):
    meta = bounded()
    meta["risk_profile"].pop("classification")
    report = linter().build_report(tmp_path, step, meta, CATALOG, False, False)
    assert any(g["gate"] == "fast_risk" and g["status"] == "blocked" for g in report["gates"])
    plan = linter().build_report(tmp_path, "plan", meta, CATALOG, False, False)
    assert not any(g["gate"] == "fast_risk" and g["status"] == "blocked" for g in plan["gates"])


def test_legacy_code_gate_does_not_gain_new_fast_requirement(tmp_path):
    meta = dict(mode="fast", semantic_decisions=[], requirement_deltas=[],
                risk_profile={"risk_flags": {"cross_component": True}, "effective_mode": "fast"})
    report = linter().build_report(tmp_path, "code", meta, CATALOG, False, False)
    assert not any(g["gate"] == "fast_risk" for g in report["gates"])


@pytest.mark.parametrize("field,value", [("requested_mode", []), ("effective_mode", "unknown"),
                                         ("override", "true"), ("risk_flags", [])])
def test_malformed_policy2_fields_are_not_runnable(field, value):
    meta = bounded()
    meta["risk_profile"][field] = value
    assert policy().assess_fast_risk(meta, CATALOG)["route"] == "blocked"


@pytest.mark.parametrize("version", [True, "2", None, 3])
def test_linter_rejects_unknown_or_wrongly_typed_versions(version):
    meta = bounded()
    meta["risk_profile"]["policy_version"] = version
    assert linter().validate_fast_risk(meta, CATALOG)


@pytest.mark.parametrize("field,value", [("needs_replan", "true"), ("severity", []), ("status", {})])
def test_malformed_delta_cannot_silently_avoid_replan(field, value):
    meta = bounded()
    meta["requirement_deltas"] = [{"severity": "major", "needs_replan": True, "status": "open", field: value}]
    assert policy().assess_fast_risk(meta, CATALOG)["route"] == "blocked"


@pytest.mark.parametrize("mode", [None, [], "invalid"])
def test_unknown_mode_is_visible_and_blocked(mode):
    meta = bounded()
    meta["mode"] = mode
    assert policy().effective_mode(meta) == "unknown"
    assert policy().assess_fast_risk(meta, CATALOG)["route"] == "blocked"


@pytest.mark.parametrize("catalog", [{}, {"fast_risk_upgrade": {}}, None])
def test_missing_catalog_cannot_authorize_fast(catalog):
    assert policy().assess_fast_risk(bounded(), catalog)["route"] == "blocked"


@pytest.mark.parametrize("flags", [None, {}, {"cross_component": False}, {"real_env_verification": True}])
def test_full_accepts_declared_boolean_subsets_without_claiming_fast_inventory(flags):
    meta = bounded()
    meta["mode"] = "full"
    meta["risk_profile"].update(requested_mode="full", effective_mode="full", risk_flags=flags)
    meta["risk_profile"].pop("classification")
    before = copy.deepcopy(meta)
    result = policy().assess_fast_risk(meta, CATALOG)
    assert result["route"] == "full"
    if flags and flags.get("real_env_verification"):
        assert "real_environment_verification" in result["required_checks"]
    assert meta == before


@pytest.mark.parametrize("flags", [{"unknown": False}, {"cross_component": 0},
                                   {"real_env_verification": "true"}, []])
def test_full_still_rejects_unknown_flags_and_nonboolean_values(flags):
    meta = bounded()
    meta["mode"] = "full"
    meta["risk_profile"]["risk_flags"] = flags
    assert policy().assess_fast_risk(meta, CATALOG)["reasons"] == ["invalid_flags"]


def test_full_subset_retains_triggers_semantic_and_major_delta_hard_gates():
    meta = bounded()
    meta["mode"] = "full"
    meta["risk_profile"].update(risk_flags={}, triggers=["real_env_verification"])
    result = policy().assess_fast_risk(meta, CATALOG)
    assert result["route"] == "full" and "real_environment_verification" in result["required_checks"]
    meta["semantic_decisions"] = [{"status": "pending"}]
    assert policy().assess_fast_risk(meta, CATALOG)["reasons"] == ["unresolved_semantic_decision"]
    meta["semantic_decisions"] = []
    meta["requirement_deltas"] = [{"severity": "major", "needs_replan": True, "status": "open"}]
    assert policy().assess_fast_risk(meta, CATALOG)["route"] == "replan"


@pytest.mark.parametrize("flags", [None, {}, {"cross_component": False}])
def test_fast_still_requires_the_complete_boolean_inventory(flags):
    meta = bounded()
    meta["risk_profile"]["risk_flags"] = flags
    assert policy().assess_fast_risk(meta, CATALOG)["reasons"] == ["invalid_flags"]
