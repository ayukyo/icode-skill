"""Versioned fast routing and optional read-only, source-bound instruction views.

The catalog owns flag/check vocabularies. An assessment is a route, never proof
that its required checks or referenced reads have actually been performed.
"""
import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat


def _profile_document(root, source, limit):
    """Read bounded canonical Markdown, rejecting links/reparse points before I/O."""
    if (not isinstance(source, str) or not 1 <= len(source) <= 512
            or "\\" in source or ":" in source or "\x00" in source
            or re.search(r'[\x00-\x1f<>"|?*]', source)
            or any(part in ("", ".", "..") or part.endswith((" ", "."))
                   for part in source.split("/"))):
        raise ValueError("invalid_source_path")
    parts = source.split("/")
    if len(parts) < 2 or parts[0] not in ("steps", "references") or not source.endswith(".md"):
        raise ValueError("invalid_source_path")
    path = root
    for part in parts:
        path = path / part
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked_source")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not stat.S_ISREG(info.st_mode):
        raise ValueError("invalid_source_path")
    if info.st_size > limit:
        raise ValueError("source_budget_exceeded")
    # Bound the actual read too: a file may grow after stat(). No cache survives
    # this call, so the next profile rebuild always detects version drift.
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("source_budget_exceeded")
    return data.decode("utf-8")


def _profile_anchors(text):
    """Index explicit HTML IDs and heading slugs once, outside fenced code."""
    counts, fence = {}, None
    for line in text.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        anchors = re.findall(r'<a\s+(?:id|name)=[\"\']([^\"\']+)[\"\']\s*></a>', line)
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            slug = re.sub(r"[^\w\- ]", "", heading.group(1).lower()).replace(" ", "-")
            anchors.append(slug)
        for anchor in anchors:
            counts[anchor] = counts.get(anchor, 0) + 1
    return counts


def build_execution_profile(metadata, step, catalog, skill_root):
    """Return a source-bound instruction view, never workflow authorization.

    Catalog contract: execution_profiles has schema_version=1,
    hash_algorithm=sha256_utf8_lf, source_sha256={canonical Markdown path: hash},
    and all six phases. Each phase has document=steps/fast.md, anchor=fast-STEP,
    requirements=[{id, source, anchor}], shared_refs=[{source, anchor}]. Phase IDs
    must occur inside their unique ICODE:FAST:STEP:BEGIN/END markers. References
    use explicit HTML IDs or unique ordinary Markdown heading slugs.

    Hash normalization applies only to version binding; rules retain original
    newlines. Source integrity is neither actual Read evidence nor admission.
    Full and legacy callers need no optional catalog. Invalid compact inputs
    return a bounded unavailable response without weakening production gates.
    """
    steps = ("plan", "review", "merge", "code", "deepcheck", "audit")
    mode = effective_mode(metadata)
    safe_step = step if isinstance(step, str) and step in steps else None

    def result(state, reason):
        return {"state": state, "effective_mode": mode, "step": safe_step, "reason": reason}

    if safe_step is None:
        return result("unavailable", "unsupported_step")
    if mode == "unknown":
        return result("unavailable", "invalid_metadata")
    profile = metadata.get("risk_profile") or {}
    version = profile.get("policy_version", 1)
    if type(version) is not int or version not in (1, 2):
        return result("unavailable", "unsupported_policy_version")
    if mode == "full":
        return result("standard", "full_preserved")
    if version == 1:
        return result("standard", "legacy_policy")

    try:
        config = catalog.get("execution_profiles") if isinstance(catalog, dict) else None
        if (not isinstance(config, dict) or type(config.get("schema_version")) is not int
                or config["schema_version"] != 1 or config.get("hash_algorithm") != "sha256_utf8_lf"):
            raise ValueError("invalid_profile_catalog")
        bindings, phases = config.get("source_sha256"), config.get("phases")
        if (not isinstance(bindings, dict) or not 1 <= len(bindings) <= 64
                or not isinstance(phases, dict) or set(phases) != set(steps)):
            raise ValueError("invalid_profile_catalog")
        root = Path(skill_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("invalid_skill_root")
        documents, anchors_by_source, total_bytes = {}, {}, 0
        for source, expected in bindings.items():
            if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
                raise ValueError("invalid_source_hash")
            text = _profile_document(root, source, 1024 * 1024)
            total_bytes += len(text.encode("utf-8"))
            if total_bytes > 4 * 1024 * 1024:
                raise ValueError("source_budget_exceeded")
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            if hashlib.sha256(normalized.encode("utf-8")).hexdigest() != expected:
                raise ValueError("source_version_mismatch")
            documents[source] = text
            anchors_by_source[source] = _profile_anchors(text)

        rules_by_step, refs_by_step, requirement_ids = {}, {}, {}
        for phase in steps:
            record = phases[phase]
            if (not isinstance(record, dict) or record.get("document") != "steps/fast.md"
                    or record.get("anchor") != "fast-" + phase):
                raise ValueError("invalid_phase")
            document = record["document"]
            if document not in documents:
                raise ValueError("unbound_source")
            text, anchor = documents[document], record["anchor"]
            begin, end = (f"<!-- ICODE:FAST:{phase}:{kind} -->" for kind in ("BEGIN", "END"))
            if text.count(begin) != 1 or text.count(end) != 1:
                raise ValueError("invalid_phase_markers")
            start, stop = text.index(begin) + len(begin), text.index(end)
            if start >= stop:
                raise ValueError("invalid_phase_markers")
            rules = text[start:stop]
            # A heading/anchor alone is an empty view. This is structural
            # completeness, not a semantic verdict on natural-language duties.
            body = re.sub(r'<a\s+(?:id|name)=[\"\'][^\"\']+[\"\']\s*></a>', "", rules)
            body = re.sub(r"(?m)^\s*#{1,6}\s+.*$", "", body)
            if not body.strip() or len(rules.encode("utf-8")) > 65536:
                raise ValueError("phase_budget_or_empty")
            if anchors_by_source[document].get(anchor) != 1 or _profile_anchors(rules).get(anchor) != 1:
                raise ValueError("invalid_phase_anchor")
            # Nested/overlapping phases must not inject another phase's rules.
            if "<!-- ICODE:FAST:" in rules:
                raise ValueError("invalid_phase_markers")
            requirements, shared = record.get("requirements"), record.get("shared_refs")
            if (not isinstance(requirements, list) or not 1 <= len(requirements) <= 256
                    or not isinstance(shared, list) or not 1 <= len(shared) <= 256):
                raise ValueError("invalid_phase_references")
            refs = [{"source": document, "anchor": anchor}]
            local_ids = set()
            for ref, is_requirement in [(r, True) for r in requirements] + [(r, False) for r in shared]:
                if not isinstance(ref, dict):
                    raise ValueError("invalid_source_reference")
                source, ref_anchor = ref.get("source"), ref.get("anchor")
                if (not isinstance(source, str) or source not in documents
                        or not isinstance(ref_anchor, str) or not 1 <= len(ref_anchor) <= 256
                        or re.search(r"[\s\x00-\x1f]", ref_anchor)
                        or anchors_by_source[source].get(ref_anchor) != 1):
                    raise ValueError("invalid_source_reference")
                clean = {"source": source, "anchor": ref_anchor}
                if is_requirement:
                    identity = ref.get("id")
                    if (not isinstance(identity, str)
                            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,95}", identity) is None
                            or identity in local_ids
                            or identity in requirement_ids and requirement_ids[identity] != (source, ref_anchor)):
                        raise ValueError("invalid_requirement_id")
                    local_ids.add(identity)
                    requirement_ids[identity] = (source, ref_anchor)
                    clean["id"] = identity
                refs.append(clean)
            refs_by_step[phase] = [dict(ref, sha256=bindings[ref["source"]],
                                             hash_algorithm="sha256_utf8_lf") for ref in refs]
            rules_by_step[phase] = rules
        source_digest = hashlib.sha256(json.dumps(bindings, sort_keys=True, separators=(",", ":"),
                                                 ensure_ascii=False).encode("utf-8")).hexdigest()
        output = dict(result("compact", "source_bound_fast"), rules=rules_by_step[step],
            source_refs=refs_by_step[step], source_digest=source_digest,
            registration={"preferred": "--paths-json", "max_members": 256,
                          "max_json_bytes": 65536, "requires_actual_read": True, "independent_phases": True})
        if len(json.dumps(output, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
            raise ValueError("profile_output_budget_exceeded")
        return output
    except (OSError, UnicodeError, TypeError, ValueError, RuntimeError):
        # Do not echo unvalidated paths, source text, or unbounded error strings.
        # The caller retains the existing authoritative step-rule route.
        return result("unavailable", "profile_validation_failed")


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
