"""Pure versioned fast routing. Filesystem evidence verification belongs to admission.

The catalog owns flag/check vocabularies. An assessment is a route, never proof
that its required checks or referenced reads have actually been performed.
"""
import hashlib
import json
import re
from pathlib import PurePosixPath, PureWindowsPath


def effective_mode(metadata):
    """Resolve recorded mode; invalid values stay visible as ``unknown``.

    This resolver does not classify risk and must not replace assess_fast_risk.
    Full is monotonic within an existing run, including legacy runs.
    """
    if not isinstance(metadata, dict):
        return "unknown"
    mode = metadata.get("mode", "full")
    profile = metadata.get("risk_profile")
    if profile is not None and not isinstance(profile, dict):
        return "unknown"
    profile = profile or {}
    requested = profile.get("requested_mode", mode)
    recorded = profile.get("effective_mode", mode)
    if any(not isinstance(value, str) or value not in ("fast", "full")
           for value in (mode, requested, recorded)):
        return "unknown"
    return "full" if "full" in (mode, requested, recorded) else "fast"


def assess_fast_risk(metadata, catalog):
    """Return {route, reasons, required_checks} without mutation or I/O.

    v2 requires explicit facts and a scope-bound evidence manifest. Callers must
    verify referenced bytes against their ticket/workspace roots before admission.
    Missing classification blocks implementation, while plan investigation remains
    available. No policy2 override can bypass semantic/replan/full hard gates.
    """
    def result(route, reasons, checks=()):
        return {"route": route, "reasons": list(dict.fromkeys(reasons)),
                "required_checks": list(dict.fromkeys(checks))}

    if not isinstance(metadata, dict):
        return result("blocked", ["invalid_metadata"])
    try:
        upgrade = catalog["fast_risk_upgrade"]
        legacy_flags = upgrade["risk_flags"]
        if not isinstance(legacy_flags, dict) or not legacy_flags:
            raise ValueError("flags")
    except (KeyError, TypeError, ValueError):
        return result("blocked", ["invalid_catalog"])

    profile = metadata.get("risk_profile")
    if profile is not None and not isinstance(profile, dict):
        return result("blocked", ["invalid_profile"])
    profile = profile or {}
    version = profile.get("policy_version", 1)
    if type(version) is not int or version not in (1, 2):
        return result("blocked", ["unsupported_policy_version"])

    if version == 1:
        # Preserve the old nine flags/triggers and explicit override semantics.
        if effective_mode(metadata) == "full":
            return result("full", ["legacy_full"])
        flags = profile.get("risk_flags") or {}
        triggers = profile.get("triggers", [])
        hit = (isinstance(flags, dict) and any(flags.get(flag, False) for flag in legacy_flags))
        hit = hit or (isinstance(triggers, list) and any(flag in legacy_flags for flag in triggers))
        if hit and profile.get("override") is not True:
            return result("full", ["legacy_risk_upgrade"])
        return result("fast", ["legacy_override" if hit else "legacy_fast"])

    if effective_mode(metadata) == "unknown":
        return result("blocked", ["invalid_mode"])
    try:
        v2 = upgrade["policy_v2"]
        full_flags = v2["full_flags"]
        blocked_flags = v2["blocked_flags"]
        replan_flags = v2["replan_flags"]
        check_map = v2["required_checks"]
        if not isinstance(full_flags, list) or not isinstance(check_map, dict):
            raise ValueError("v2")
        known = list(legacy_flags) + full_flags
    except (KeyError, TypeError, ValueError):
        return result("blocked", ["invalid_catalog"])
    flags = profile.get("risk_flags")
    preserved_full = effective_mode(metadata) == "full"
    if flags is None and preserved_full:
        # Full execution does not assert bounded-fast eligibility. No fast risk
        # inventory is required until shorter execution is requested; supplied
        # facts and semantic/delta blockers still take the normal checks below.
        flags = {}
    if (not isinstance(flags, dict) or not set(flags).issubset(known)
            or any(type(v) is not bool for v in flags.values())
            or (not preserved_full and set(flags) != set(known))):
        return result("blocked", ["invalid_flags"])
    triggers = profile.get("triggers", [])
    if not isinstance(triggers, list) or any(not isinstance(t, str) or t not in known for t in triggers):
        return result("blocked", ["invalid_triggers"])
    if type(profile.get("override", False)) is not bool:
        return result("blocked", ["invalid_override"])
    hit = {flag for flag in known if flags.get(flag, False)} | set(triggers)
    checks = [check for flag in known if flag in hit for check in check_map.get(flag, [])]

    decisions = metadata.get("semantic_decisions", [])
    deltas = metadata.get("requirement_deltas", [])
    if not isinstance(decisions, list) or any(not isinstance(d, dict) for d in decisions):
        return result("blocked", ["invalid_semantic_decisions"], checks)
    if not isinstance(deltas, list) or any(not isinstance(d, dict) for d in deltas):
        return result("blocked", ["invalid_requirement_deltas"], checks)
    if any(("needs_replan" in d and type(d["needs_replan"]) is not bool)
           or any(key in d and not isinstance(d[key], str) for key in ("severity", "status"))
           for d in deltas):
        return result("blocked", ["invalid_requirement_deltas"], checks)
    if any(d.get("status") != "resolved" for d in decisions) or hit.intersection(blocked_flags):
        return result("blocked", ["unresolved_semantic_decision"], checks)
    if any(d.get("severity") == catalog["constants"]["major_delta_severity"]
           and d.get("needs_replan") is True and d.get("status") != "resolved" for d in deltas) or hit.intersection(replan_flags):
        return result("replan", ["major_requirement_delta"], checks)
    if effective_mode(metadata) == "full":
        return result("full", ["full_preserved"], checks)
    hard = [flag for flag in full_flags if flag in hit]
    if hard:
        return result("full", hard, checks)

    classification = profile.get("classification")
    if classification is None:
        return result("blocked", ["missing_classification"], checks)
    if not isinstance(classification, dict) or classification.get("scope") not in v2["classification_scopes"]:
        return result("blocked", ["invalid_classification"], checks)
    scope = metadata.get("scope_contract")
    if not isinstance(scope, dict) or any(not isinstance(scope.get(key), str) or not scope[key].strip()
            for key in v2["classification_required_scope_fields"]):
        return result("blocked", ["missing_scope_contract"], checks)
    try:
        digest = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":"),
                                           ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        return result("blocked", ["missing_scope_contract"], checks)
    if classification.get("scope_digest") != digest:
        return result("blocked", ["scope_digest_mismatch"], checks)
    refs = classification.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        return result("blocked", ["invalid_evidence_refs"], checks)
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("scope") not in v2["classification_ref_scopes"]:
            return result("blocked", ["invalid_evidence_refs"], checks)
        path, sha = ref.get("path"), ref.get("sha256")
        if not isinstance(path, str) or not path.strip() or "\\" in path or ":" in path or "\x00" in path \
                or PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute() \
                or any(part in ("..", ".", "") for part in path.split("/")) \
                or not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
            return result("blocked", ["invalid_evidence_refs"], checks)
    if classification["scope"] == "unbounded":
        return result("full", ["unbounded_scope"], checks)
    return result("fast", ["bounded_scope"], checks)
