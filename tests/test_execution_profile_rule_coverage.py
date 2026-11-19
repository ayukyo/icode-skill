"""Compact execution views must keep every rule the workflow registers.

Guard for extending ``execution_profiles`` beyond ``fast``: a compact view is
allowed to shorten prose, never to drop a duty. Three invariants are enforced
against the real catalog and the real documents:

1. every requirement ID registered for a phase appears verbatim in that phase's
   compact text (what the agent is actually told to follow);
2. every requirement source/anchor resolves exactly once in its source document;
3. every rule ID declared in ``steps/*.md`` is registered in the catalog, and
   nothing is registered that no document declares.

Invariant 3 is the activation point for full/log/init compaction. Adding an ID
such as ``PLAN-SCOPE`` to ``steps/01_plan.md`` fails this test until the phase
record and the compact text cover it too - deliberately: otherwise "no rule was
lost" would be unprovable.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STEPS = ("plan", "review", "merge", "code", "deepcheck", "audit")
RULE_ID = re.compile(r"(?m)^\s*-\s+\*\*([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\*\*")


def policy_module():
    spec = importlib.util.spec_from_file_location("rule_coverage_policy",
                                                  ROOT / "tools/fast_policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def catalog():
    return json.loads((ROOT / "mcp/workflow-gate/gates.json").read_text(encoding="utf-8"))


def declared_rule_ids():
    """Rule IDs declared with the canonical ``- **ID**`` bullet markup."""
    found = {}
    for path in sorted((ROOT / "steps").glob("*.md")):
        for identity in RULE_ID.findall(path.read_text(encoding="utf-8")):
            found.setdefault(identity, set()).add(path.name)
    return found


def registered_requirements():
    phases = catalog()["execution_profiles"]["phases"]
    return {record["id"]: (phase, record["source"], record["anchor"])
            for phase, phase_record in phases.items()
            for record in phase_record["requirements"]}


def build(step, mode="fast"):
    metadata = {"mode": mode,
                "risk_profile": {"policy_version": 2, "requested_mode": mode,
                                 "effective_mode": mode}}
    return policy_module().build_execution_profile(metadata, step, catalog(), ROOT)


def test_rule_id_scanner_finds_the_declared_fast_ids():
    """Self-test: a broken pattern must not make the coverage checks vacuous."""
    declared = declared_rule_ids()
    assert len(declared) == 17, sorted(declared)
    assert all(identity.startswith("FAST-") for identity in declared)
    assert declared["FAST-PLAN-SCOPE"] == {"fast.md"}


def test_every_declared_rule_id_is_registered_in_the_catalog():
    declared, registered = set(declared_rule_ids()), set(registered_requirements())
    assert declared - registered == set(), (
        "rule IDs declared in steps/*.md but missing from execution_profiles: "
        f"{sorted(declared - registered)}")
    assert registered - declared == set(), (
        "requirement IDs registered but declared nowhere in steps/*.md: "
        f"{sorted(registered - declared)}")


@pytest.mark.parametrize("step", STEPS)
def test_compact_phase_keeps_every_registered_requirement(step):
    profile = build(step)
    assert profile["state"] == "compact", profile
    rules = profile["rules"]
    expected = [identity for identity, (phase, _source, _anchor) in registered_requirements().items()
                if phase == step]
    assert expected, f"phase {step} registers no requirement"
    missing = [identity for identity in expected if identity not in rules]
    assert missing == [], f"compact text for {step} dropped registered rules: {missing}"


def test_registered_anchors_resolve_once_in_their_source():
    module = policy_module()
    offenders = []
    for identity, (_phase, source, anchor) in registered_requirements().items():
        path = ROOT / source
        if not path.is_file():
            offenders.append((identity, source, "missing source"))
            continue
        anchors = module._profile_anchors(path.read_text(encoding="utf-8"))
        if anchors.get(anchor) != 1:
            offenders.append((identity, source, f"anchor {anchor!r} count {anchors.get(anchor)}"))
    assert offenders == [], offenders


def test_full_mode_is_never_compacted_implicitly():
    """Full keeps the standard view; compaction for full needs an explicit change."""
    for step in STEPS:
        profile = build(step, mode="full")
        assert profile["state"] == "standard", profile
        assert profile["reason"] == "full_preserved"