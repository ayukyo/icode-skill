"""Read-only verification applicability and append-only evidence validation.

These functions operate on the existing verification_runs and event ledger;
there is no independent verification lifecycle or writer here.
"""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import time

ENVIRONMENTS = {"host", "physical", "simulated", "unknown"}
PENDING_CONTRACT = {"required": True, "requirements_pending": True,
                    "required_layers": [], "required_consumers": [], "required_scenarios": []}
PROVENANCE_FIELDS = ("build_provenance", "deploy_provenance", "runtime_provenance")
_CONTROL = None


class EvidenceError(ValueError):
    pass


def control_module():
    global _CONTROL
    if _CONTROL is None:
        spec = importlib.util.spec_from_file_location("verification_control_reader", Path(__file__).with_name("icode_control.py"))
        _CONTROL = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_CONTROL)
    return _CONTROL


def enabled(meta):
    return (meta.get("candidate_tracking") or {}).get("mode") == "enabled"


def real_env_risk(meta):
    profile = meta.get("risk_profile") or {}
    if not isinstance(profile, dict):
        return False
    for key in ("risk_flags", "flags"):
        flags = profile.get(key)
        if isinstance(flags, dict) and flags.get("real_env_verification") is True:
            return True
    triggers = profile.get("triggers") or []
    return isinstance(triggers, list) and "real_env_verification" in triggers


def pending_contract():
    return {key: list(value) if isinstance(value, list) else value for key, value in PENDING_CONTRACT.items()}


def real_env_contract_required(meta):
    """Legacy untracked declarations are not migrated by a read-only query."""
    contract = meta.get("verification_contract")
    return real_env_risk(meta) and (enabled(meta) or isinstance(contract, dict) and "requirements_pending" in contract)


def build_starts(meta):
    extensions = meta.get("extensions") or {}
    namespace = extensions.get("verification") if isinstance(extensions, dict) else None
    return (namespace.get("build_starts") or []) if isinstance(namespace, dict) else []


def event_runs(events):
    return [{key: value for key, value in (event.get("payload") or {}).items() if key != "metadata_hash_after"}
            for event in events if event.get("event_type") == "verification_recorded"]


def context(out_dir, meta, *, capture=None, verify=None, candidate_api=None):
    """Read-only context. Enabled tickets without a live root fail closed."""
    result = {"enabled": enabled(meta), "current": None, "events": [], "reasons": [], "candidate_api": candidate_api}
    if not result["enabled"]:
        return result
    if out_dir is None:
        result["reasons"] = ["candidate_ticket_dir_missing"]
        return result
    api = None
    try:
        if capture is None or verify is None or candidate_api is None:
            api = control_module()
            capture, verify, candidate_api = api.candidate_snapshot, api.verify_event_chain, api.candidate_module()
        result["candidate_api"] = candidate_api
        events, problems = verify(out_dir, meta)
        if problems:
            result["reasons"] = ["event_chain"]
            return result
        result["events"] = events
        result["current"] = capture(out_dir, meta)
    except Exception as exc:
        # No source identity is preferable to promoting a historical pass.
        result["reasons"] = ["candidate_capture_unavailable"]
        result["detail"] = str(exc)
    return result


def resolve_candidate(candidate_id, meta, events, live, candidate_api):
    if not isinstance(candidate_id, str) or re.fullmatch(r"[0-9a-f]{64}", candidate_id) is None:
        raise EvidenceError("enabled verification requires an explicit --candidate-id")
    candidates = [(meta.get("candidate_tracking") or {}).get("current"), live]
    candidates += [receipt.get("candidate") for receipt in meta.get("review_receipts") or []]
    candidates += [run.get("candidate") for run in event_runs(events)]
    candidates += [receipt.get("source_candidate") for receipt in build_starts(meta)]
    # Explicit capture history is retained in real controlled metadata events.
    for event in events:
        payload = event.get("payload") or {}
        tracking = payload.get("candidate_tracking") or ((payload.get("set") or {}).get("candidate_tracking"))
        if isinstance(tracking, dict):
            candidates.append(tracking.get("current"))
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("candidate_id") == candidate_id \
                and candidate_api.snapshot_identity_ok(candidate):
            return candidate
    raise EvidenceError("candidate_id has no verifiable snapshot or matching live source")


def cell(run):
    return run.get("layer"), run.get("consumer"), run.get("scenario")


def concrete(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().lower() not in {
        "*", "unknown", "physical", "device", "scenario", "todo", "pending", "<device>", "<scenario>"}


def validate_source_claim(run, tracked, source_runs):
    """One rule for writers, event mirrors and current evidence eligibility."""
    if not tracked:
        return
    if run.get("layer") == "build" and run.get("kind") != "build":
        raise EvidenceError("build_cell_requires_actual_build_kind")
    if run.get("outcome") == "pass" and run.get("kind") == "build" and run.get("build_provenance") is None:
        raise EvidenceError("build pass requires build-start-backed artifact provenance")
    if run.get("build_source") != "fresh" or run.get("build_provenance") is not None:
        return
    for field in ("deploy_provenance", "runtime_provenance"):
        provenance = run.get(field)
        if not isinstance(provenance, dict):
            continue
        origin = next((item for item in source_runs if item.get("run_id") == provenance.get("origin_build_run_id")), None)
        if origin is not None and origin.get("kind") == "build" and origin.get("outcome") == "pass" \
                and origin.get("build_source") == "fresh" and origin.get("build_provenance") is not None:
            return  # Full source/hash/receipt matching is validated below.
    # Failed attempts without build artifacts can still be recorded as fail
    # with an unknown source; failure does not authenticate a fresh label.
    raise EvidenceError("fresh_claim_requires_controlled_build_source")


def eligible_latest_runs(meta, ctx, *, source_runs=None):
    """Filter candidate and environment BEFORE selecting the latest cell.

    A runner-only pass cannot supersede a direct executable result in that
    same cell. Later negative evidence is kept, including failures before an
    executable can run. A real direct execution can supersede its failure.
    """
    latest, rejected, candidates = {}, [], []
    runs = meta.get("verification_runs") or []
    if not isinstance(runs, list):
        return {"latest": {}, "rejected": [], "reasons": ["verification_runs_invalid"]}
    if ctx["enabled"] and ctx["reasons"]:
        return {"latest": {}, "rejected": [], "reasons": list(ctx["reasons"])}
    source_runs = event_runs(ctx.get("events") or []) if source_runs is None else source_runs
    for run in runs:
        if not isinstance(run, dict):
            continue
        reason = None
        if ctx["enabled"]:
            snapshot = run.get("candidate")
            if run.get("candidate_id") != (snapshot or {}).get("candidate_id"):
                reason = "candidate_identity_missing"
            elif not ctx["candidate_api"].compare_candidates(snapshot, ctx["current"])["equivalent"]:
                reason = "candidate_drift"
            elif run.get("environment") not in ENVIRONMENTS:
                reason = "environment_missing"
            if reason is None:
                try:
                    validate_source_claim(run, True, source_runs)
                except EvidenceError as exc:
                    reason = str(exc)
        # Explicit new environments are honored on old tickets as well;
        # absent environments preserve the old, untracked contract behavior.
        if reason is None and run.get("layer") == "physical" and \
                (ctx["enabled"] or run.get("environment") is not None) and run.get("environment") != "physical":
            reason = "physical_environment_required"
        if reason is None and real_env_contract_required(meta) and run.get("layer") == "physical" and \
                (not concrete(run.get("device")) or not concrete(run.get("scenario"))):
            reason = "concrete_device_scenario_required"
        if reason:
            rejected.append({"run_id": run.get("run_id"), "reason": reason})
        else:
            candidates.append(run)
    direct_cells = {cell(run) for run in candidates if (run.get("direct_test") or {}).get("binary_executed") is True}
    for run in candidates:
        key = cell(run)
        direct = run.get("direct_test") or {}
        if key in direct_cells and run.get("outcome") == "pass" and direct.get("binary_executed") is not True:
            rejected.append({"run_id": run.get("run_id"), "reason": "direct_execution_required"})
            continue
        latest[key] = run
    return {"latest": latest, "rejected": rejected, "reasons": []}


def required_cells(contract):
    dimensions = []
    for key in ("required_layers", "required_consumers", "required_scenarios"):
        values = contract.get(key)
        if not isinstance(values, list) or not values or any(not isinstance(x, str) or not x.strip() for x in values) \
                or len(values) != len(set(values)):
            return None
        dimensions.append(values)
    if contract.get("required_cells") is None:
        return [(layer, consumer, scenario) for layer in dimensions[0] for consumer in dimensions[1] for scenario in dimensions[2]]
    cells = contract["required_cells"]
    if not isinstance(cells, list) or not cells:
        return None
    keys = []
    for row in cells:
        if not isinstance(row, dict) or set(row) != {"layer", "consumer", "scenario"}:
            return None
        key = cell(row)
        if any(value not in dimensions[index] for index, value in enumerate(key)):
            return None
        keys.append(key)
    return keys if len(keys) == len(set(keys)) else None


def metric_settings(contract, cells):
    """Validate existing metric/profile contract before any verdict is made."""
    profile = contract.get("profile", "generic")
    metrics = contract.get("required_metrics", [])
    if profile not in {"generic", "embedded", "camera"} or not isinstance(metrics, list):
        return None
    required = {"name", "unit", "operator", "value", "layer", "consumer", "scenario"}
    seen = set()
    for metric in metrics:
        if not isinstance(metric, dict) or not required.issubset(metric) or \
                any(not isinstance(metric[key], str) or not metric[key].strip() for key in required - {"value"}):
            return None
        key = (*cell(metric), metric["name"])
        value = metric["value"]
        if key in seen or metric["operator"] not in {"lt", "lte", "gt", "gte", "eq"} or key[:3] not in cells \
                or type(value) not in (int, float) or not math.isfinite(value):
            return None
        seen.add(key)
    baseline_ref = contract.get("baseline_ref")
    if (profile in {"embedded", "camera"} or baseline_ref is not None) and \
            (not isinstance(baseline_ref, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", baseline_ref) is None):
        return None
    return profile, baseline_ref, metrics


def unit_state(run, profile="generic", metrics=(), baseline_ref=None):
    if run is None:
        return "run_missing"
    if run.get("outcome") != "pass":
        return "latest_outcome_" + str(run.get("outcome", "unknown"))
    if not isinstance(run.get("evidence"), str) or not run["evidence"].strip():
        return "evidence_missing"
    if not isinstance(run.get("baseline"), str) or not run["baseline"].strip():
        return "baseline_missing"
    if profile in {"embedded", "camera"} or baseline_ref is not None:
        if run.get("profile") != profile:
            return "profile_mismatch"
        if run.get("baseline_ref") != baseline_ref:
            return "baseline_ref_mismatch"
    comparisons = {"lt": lambda a, b: a < b, "lte": lambda a, b: a <= b, "gt": lambda a, b: a > b,
                   "gte": lambda a, b: a >= b, "eq": lambda a, b: a == b}
    for metric in metrics:
        values = run.get("metrics")
        actual = values.get(metric["name"]) if isinstance(values, dict) else None
        if type(actual) not in (int, float) or not math.isfinite(actual):
            return "metric_missing:" + metric["name"]
        if not comparisons[metric["operator"]](actual, metric["value"]):
            return "metric_threshold_failed:" + metric["name"]
    return "satisfied"


def assess(meta, ctx):
    selected = eligible_latest_runs(meta, ctx)
    result = {"ok": False, "state": "verification_pending", "current_candidate_id": (ctx["current"] or {}).get("candidate_id"),
              "reasons": list(selected["reasons"]), "eligible_runs": [{key: value for key, value in run.items() if key != "candidate"}
                       for run in selected["latest"].values()], "rejected_runs": selected["rejected"]}
    if result["reasons"]:
        result["state"] = "blocked"
        return result
    contract = meta.get("verification_contract")
    if isinstance(contract, dict) and contract.get("requirements_pending") is True:
        result.update(state="requirements_pending", reasons=["requirements_pending"])
        return result
    if real_env_contract_required(meta) and (not isinstance(contract, dict) or contract.get("required") is not True):
        result.update(state="requirements_pending", reasons=["real_env_contract_required"])
        return result
    if contract is None or (isinstance(contract, dict) and contract.get("required") is False):
        state = "not_required" if contract else "legacy_untracked" if real_env_risk(meta) and not ctx["enabled"] else "untracked"
        result.update(ok=True, state=state)
        return result
    cells = required_cells(contract) if isinstance(contract, dict) else None
    if cells is None or (real_env_contract_required(meta) and not any(key[0] == "physical" for key in cells)):
        result.update(state="invalid_contract", reasons=["verification_contract_invalid"])
        return result
    settings = metric_settings(contract, cells)
    if contract.get("required") is not True or settings is None:
        result.update(state="invalid_contract", reasons=["verification_contract_invalid"])
        return result
    profile, baseline_ref, required_metrics = settings
    for key in cells:
        metrics = [metric for metric in required_metrics if cell(metric) == key]
        state = unit_state(selected["latest"].get(key), profile, metrics, baseline_ref)
        if state != "satisfied":
            result["reasons"].append(state)
    result["ok"] = not result["reasons"]
    result["state"] = "satisfied" if result["ok"] else "verification_pending"
    return result


def scope_hash(candidate, paths):
    if not isinstance(paths, list) or not paths or paths != sorted(set(paths)):
        raise EvidenceError("reuse.scope_paths must be nonempty sorted unique paths")
    manifest = {row["path"]: row for row in candidate["manifest"]}
    if any(path not in manifest for path in paths):
        raise EvidenceError("reuse scope path is missing from source snapshot")
    rows = [manifest[path] for path in paths]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def validate_documentation_scope(previous, current, paths, digest, candidate_api):
    """Pure proof shared by a doc transfer and later source association."""
    if not candidate_api.snapshot_identity_ok(previous) or not candidate_api.snapshot_identity_ok(current):
        raise EvidenceError("reuse candidate identity is unavailable")
    contract_keys = ("identity", "base", "code_files", "scope_contract", "inspection_scope", "excluded_side_effects", "control_artifact_exclusions")
    if any(previous[key] != current[key] for key in contract_keys):
        raise EvidenceError("reuse source identity or scope contract changed")
    old_rows = {row["path"]: row for row in previous["manifest"]}
    new_rows = {row["path"]: row for row in current["manifest"]}
    changed = [path for path in old_rows.keys() | new_rows.keys() if old_rows.get(path) != new_rows.get(path)]
    # CMake, config and dependency changes cannot gain a narrative exemption.
    if any(Path(path).suffix.lower() not in {".md", ".rst"} for path in changed):
        raise EvidenceError("reuse permits only documentation changes")
    if scope_hash(previous, paths) != digest or scope_hash(current, paths) != digest:
        raise EvidenceError("reuse scope_hash does not match unchanged verified scope")


def reuse_source_related(previous, later, reuse, candidate_api):
    # Equivalence is directional: the later commit extends the origin HEAD.
    if candidate_api.compare_candidates(previous, later)["equivalent"]:
        return True
    try:
        validate_documentation_scope(previous, later, reuse["scope_paths"], reuse["scope_hash"], candidate_api)
        return True
    except EvidenceError:
        return False


def validate_reuse(run, runs, candidate_api):
    reuse = run.get("reuse")
    if reuse is None:
        return
    required = {"origin_run_id", "reason", "scope_paths", "scope_hash", "comparison_ref"}
    if not isinstance(reuse, dict) or set(reuse) != required or any(not isinstance(reuse[key], str) or not reuse[key].strip()
            for key in required - {"scope_paths"}):
        raise EvidenceError("reuse requires structured origin/reason/scope/hash/comparison_ref")
    origin = next((item for item in runs if item.get("run_id") == reuse["origin_run_id"]), None)
    if origin is None or cell(origin) != cell(run) or origin.get("outcome") != "pass":
        raise EvidenceError("reuse origin must be an existing successful same-cell event")
    for field in ("outcome", "metrics", "artifact_identity", "environment", "device", "window", "profile", "baseline_ref", *PROVENANCE_FIELDS, "direct_test"):
        if origin.get(field) != run.get(field):
            raise EvidenceError("reuse origin identity/outcome/metrics/provenance mismatch: " + field)
    previous, current = origin.get("candidate"), run.get("candidate")
    validate_documentation_scope(previous, current, reuse["scope_paths"], reuse["scope_hash"], candidate_api)
    # Reuse can transfer an applicable success across a documentation change,
    # never resurrect a success invalidated by a later eligible negative run.
    # Compare against the origin source and environment before reading outcomes.
    history = []
    for item in runs:
        if item.get("environment") == origin.get("environment") and cell(item) == cell(origin) \
                and reuse_source_related(previous, item.get("candidate"), reuse, candidate_api):
            # Keep stored snapshots intact. This in-memory normalization only
            # lets the common environment/direct qualification filter run after
            # source association has been proven in the correct direction.
            history.append(dict(item, candidate=previous, candidate_id=previous["candidate_id"]))
    source_context = {"enabled": True, "current": previous, "events": [], "reasons": [], "candidate_api": candidate_api}
    selection = eligible_latest_runs({"verification_runs": history}, source_context, source_runs=runs)
    rejected = {item["run_id"] for item in selection["rejected"]}
    if origin["run_id"] in rejected:
        raise EvidenceError("reuse_origin_no_longer_eligible")
    origin_index = runs.index(origin)
    related = {item["run_id"] for item in history}
    for later in runs[origin_index + 1:]:
        if later.get("run_id") in related and later.get("run_id") not in rejected and later.get("outcome") != "pass":
            raise EvidenceError("reuse_origin_invalidated_by_later_" + str(later.get("outcome")))


def validate_supersedes(run, runs):
    identifier = run.get("supersedes_run_id")
    if identifier is not None and not any(item.get("run_id") == identifier and cell(item) == cell(run) for item in runs):
        raise EvidenceError("supersedes_run_id must reference an existing same-cell run")


def validate_direct(run):
    direct = run.get("direct_test")
    if direct is None:
        return
    required = {"target_built", "binary_executed", "test_runner_discovered", "ci_registered", "binary_exit_code"}
    if not isinstance(direct, dict) or set(direct) != required or any(type(direct[key]) is not bool for key in required - {"binary_exit_code"}):
        raise EvidenceError("direct_test requires four independent booleans and binary_exit_code")
    code = direct["binary_exit_code"]
    if direct["binary_executed"]:
        if not direct["target_built"] or type(code) is not int:
            raise EvidenceError("executed binary requires built target and observed integer exit code")
        if run.get("outcome") == "pass" and code != 0:
            raise EvidenceError("runner pass cannot override a failing directly executed binary")
    elif code is not None:
        raise EvidenceError("unexecuted binary cannot claim an exit code")


def safe_directory(raw, workspace):
    root = Path(workspace).resolve()
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    # The explicitly declared output root can live outside the Git checkout.
    # Read only its directory state, never arbitrary system/private directories.
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise EvidenceError("build directory symlinks are prohibited")
    path = path.resolve(strict=True)
    forbidden = {".git", ".ssh", ".aws", ".gnupg", ".codex", ".claude", ".config", ".local"}
    if path == root or path in root.parents or path == Path.home().resolve() or any(part in forbidden for part in path.parts) \
            or path.name == ".icode_output" or re.fullmatch(r"\.icode_output_\d+", path.name) \
            or (path / ".ico_metadata.json").exists():
        raise EvidenceError("build directory must be a dedicated output directory, not source/control/private roots")
    try:
        path.relative_to(root)
    except ValueError:
        # External user-owned output roots are allowed. System trees are not
        # designated build areas even when the assistant happens to run as root.
        if path.parts[1:2] and path.parts[1] in {"etc", "proc", "sys", "dev", "root", "boot", "usr", "bin", "sbin", "lib", "lib64"}:
            raise EvidenceError("system directories cannot be build receipt roots")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise EvidenceError("build directory is not a directory")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise EvidenceError("external build directory must be owned by the current user")
    return path, info


def artifact_hashes(artifacts):
    """Artifact identity is independent of copy/load paths; retain multiplicity."""
    if not isinstance(artifacts, list) or not artifacts:
        raise EvidenceError("provenance requires nonempty artifacts")
    hashes, paths = [], set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"} or not isinstance(artifact["path"], str) \
                or not artifact["path"] or "\0" in artifact["path"] or artifact["path"] in paths \
                or not isinstance(artifact["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is None:
            raise EvidenceError("artifact must contain path and lowercase SHA256")
        paths.add(artifact["path"])
        hashes.append(artifact["sha256"])
    return sorted(hashes)


def artifact_read_budget(candidate_api, limits=None):
    """Transient accounting using the ticket's existing candidate bounds."""
    bounds = candidate_api.normalize_limits(limits)
    return {"limits": bounds, "files": 0, "bytes": 0,
            "deadline": time.monotonic() + bounds["timeout_seconds"]}


def check_artifact_timeout(budget):
    if time.monotonic() >= budget["deadline"]:
        raise EvidenceError("artifact read timeout exceeded")


def artifact_facts(artifacts, workspace, *, build_dir=None, observed_ns=None, budget=None):
    if budget is None:
        budget = artifact_read_budget(control_module().candidate_module())
    hashes = artifact_hashes(artifacts)
    root = Path(workspace).resolve()
    seen = set()
    for artifact in artifacts:
        check_artifact_timeout(budget)
        budget["files"] += 1
        if budget["files"] > budget["limits"]["max_files"]:
            raise EvidenceError("artifact file count exceeds candidate max_files")
        path = root / artifact["path"]
        private = {".git", ".ssh", ".aws", ".gnupg", ".codex", ".claude", ".config"}
        if ".." in Path(artifact["path"]).parts or any(part in private for part in path.parts) \
                or any(parent.is_symlink() for parent in (path, *path.parents)):
            raise EvidenceError("artifact path must be a safe explicitly declared local file")
        path = path.resolve(strict=True)
        if not Path(artifact["path"]).is_absolute():
            path.relative_to(root)
        elif build_dir is None and path.parts[1] in {"etc", "proc", "sys", "dev", "root", "boot", "usr", "bin", "sbin", "lib", "lib64"}:
            raise EvidenceError("system/private paths cannot be local deployment artifacts")
        if path in seen:
            raise EvidenceError("duplicate artifact path")
        seen.add(path)
        if build_dir is not None:
            try:
                path.relative_to(build_dir)
            except ValueError as exc:
                raise EvidenceError("fresh artifact is outside the observed build directory") from exc
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise EvidenceError("artifact is not a regular file")
        if hasattr(os, "getuid") and before.st_uid != os.getuid():
            raise EvidenceError("local artifact must be owned by the current user")
        # Reproducible builds may normalize mtime to SOURCE_DATE_EPOCH.
        # ctime still binds the local file to the pre-build observation;
        # both timestamps remain part of the immutable-read check below.
        if observed_ns is not None and before.st_ctime_ns < observed_ns:
            raise EvidenceError("artifact predates build-start receipt")
        if budget["bytes"] + before.st_size > budget["limits"]["max_bytes"]:
            raise EvidenceError("artifact bytes exceed candidate max_bytes")
        digest = hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise EvidenceError("artifact replaced during read")
            while True:
                check_artifact_timeout(budget)
                # A bounded extra byte detects growth past the remaining
                # budget without reading an unbounded newly enlarged file.
                remaining = budget["limits"]["max_bytes"] - budget["bytes"]
                block = stream.read(min(65536, remaining + 1))
                budget["bytes"] += len(block)
                if budget["bytes"] > budget["limits"]["max_bytes"]:
                    raise EvidenceError("artifact bytes exceed candidate max_bytes")
                check_artifact_timeout(budget)
                if not block:
                    break
                digest.update(block)
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != \
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise EvidenceError("artifact changed during read")
        if digest.hexdigest() != artifact["sha256"]:
            raise EvidenceError("artifact SHA256 differs from actual local bytes")
    return hashes


def validate_provenance(run, meta, events, workspace, candidate_api, *, check_files=True, live_candidate=None):
    validate_source_claim(run, enabled(meta), event_runs(events))
    budget = artifact_read_budget(candidate_api, (meta.get("candidate_tracking") or {}).get("limits")) if check_files else None
    for field in PROVENANCE_FIELDS:
        provenance = run.get(field)
        if provenance is None:
            continue
        required = {"source_candidate_id", "artifacts", "evidence_refs"}
        allowed = required | ({"start_event_id", "configure_command", "build_command", "compiler", "architecture"} if field == "build_provenance" else {"origin_build_run_id"})
        if not isinstance(provenance, dict) or not required.issubset(provenance) or set(provenance) - allowed:
            raise EvidenceError("invalid structured " + field)
        if not isinstance(provenance["evidence_refs"], list) or not provenance["evidence_refs"] or \
                any(not isinstance(ref, str) or not ref.strip() for ref in provenance["evidence_refs"]):
            raise EvidenceError(field + " requires evidence_refs")
        source_id = provenance["source_candidate_id"]
        provenance_candidate = run.get("candidate")
        if source_id != run.get("candidate_id"):
            provenance_candidate = resolve_candidate(source_id, meta, events, None, candidate_api)
            equivalent = candidate_api.compare_candidates(provenance_candidate, run.get("candidate"))["equivalent"]
            origin = next((item for item in event_runs(events) if item.get("run_id") == (run.get("reuse") or {}).get("origin_run_id")), None)
            reused_origin = origin is not None and origin.get(field) == provenance
            if not equivalent and not reused_origin:
                raise EvidenceError(field + " source candidate does not match run or proven reuse origin")
        directory, observed_ns = None, None
        if field == "build_provenance":
            if run.get("build_source") == "fresh" and run.get("outcome") == "pass" and live_candidate is not None \
                    and not candidate_api.compare_candidates(run.get("candidate"), live_candidate)["equivalent"]:
                raise EvidenceError("fresh source candidate drifted since build-start")
            for key in ("configure_command", "build_command", "compiler", "architecture"):
                if not provenance.get(key):
                    raise EvidenceError("build provenance requires actual commands/compiler/architecture")
            start = next((event for event in events if event.get("event_id") == provenance.get("start_event_id") and
                          event.get("event_type") == "metadata_updated" and (event.get("payload") or {}).get("build_start_control") == 1), None)
            if start is None:
                raise EvidenceError("build provenance has no controlled start_event_id")
            receipt = (start["payload"].get("build_start_receipt") or {})
            if not candidate_api.compare_candidates(receipt.get("source_candidate"), provenance_candidate)["equivalent"]:
                raise EvidenceError("source candidate drifted since build-start")
            for key in ("configure_command", "build_command"):
                if provenance[key] != (receipt.get("parameters") or {}).get(key):
                    raise EvidenceError("build command does not match pre-build receipt")
            if run.get("build_source") == "fresh":
                if receipt.get("directory", {}).get("empty") is not True:
                    raise EvidenceError("fresh build requires a directory observed empty before start")
                observed_ns = receipt.get("observed_ns")
            if check_files:
                directory, info = safe_directory(receipt["build_dir"], workspace)
                if (info.st_dev, info.st_ino) != (receipt["directory"]["device"], receipt["directory"]["inode"]):
                    raise EvidenceError("build directory identity changed")
        else:
            origin = next((item for item in event_runs(events) if item.get("run_id") == provenance.get("origin_build_run_id") and item.get("kind") == "build" and item.get("outcome") == "pass"), None)
            if origin is None or not origin.get("build_provenance") or artifact_hashes(origin["build_provenance"]["artifacts"]) != artifact_hashes(provenance["artifacts"]) \
                    or not candidate_api.compare_candidates(origin.get("candidate"), provenance_candidate)["equivalent"]:
                raise EvidenceError(field + " does not match a real build artifact/source event")
        hashes = artifact_facts(provenance["artifacts"], workspace, build_dir=directory, observed_ns=observed_ns, budget=budget) if check_files and field != "runtime_provenance" else artifact_hashes(provenance["artifacts"])
        # Remote runtime hashes are event/evidence claims; no device access is
        # performed here. Only build/deploy local paths are machine-read.
        identity = run.get("artifact_identity")
        if len(hashes) == 1 and identity != "sha256:" + str(hashes[0]):
            raise EvidenceError("artifact_identity disagrees with structured artifact SHA256")


def event_issues(events, meta, candidate_api):
    """Offline mirror checking, including writer-only build-start markers."""
    problems, starts, runs, tracking_enabled = [], [], [], False
    for position, event in enumerate(events):
        payload = event.get("payload") or {}
        if event.get("event_type") == "ticket_created" and (payload.get("candidate_tracking") or {}).get("mode") == "enabled":
            tracking_enabled = True
        if event.get("event_type") == "metadata_updated" and ((payload.get("set") or {}).get("candidate_tracking") or {}).get("mode") == "enabled":
            tracking_enabled = True
        if event.get("event_type") == "ticket_created" and build_starts(payload):
            problems.append("build_start_birth_forbidden")
        if event.get("event_type") == "metadata_updated":
            updates = payload.get("set") or {}
            nested = build_starts(updates)
            if payload.get("build_start_control") is not None:
                receipt = payload.get("build_start_receipt")
                if type(payload["build_start_control"]) is not int or payload["build_start_control"] != 1 or event.get("actor") != "icode" \
                        or not event.get("request_id") or not isinstance(receipt, dict) or receipt.get("event_id") != event.get("event_id") \
                        or nested != [*starts, receipt] or not candidate_api.snapshot_identity_ok(receipt.get("source_candidate")):
                    problems.append("build_start_control_event_shape")
                else:
                    starts.append(receipt)
            elif "extensions" in updates and nested != starts:
                problems.append("build_start_writer_bypassed")
        if event.get("event_type") != "verification_recorded":
            continue
        run = {key: value for key, value in payload.items() if key != "metadata_hash_after"}
        if tracking_enabled and (run.get("candidate") is None or run.get("environment") not in ENVIRONMENTS):
            problems.append("verification_candidate_environment_missing")
        if run.get("candidate") is not None and (not candidate_api.snapshot_identity_ok(run["candidate"]) or run.get("candidate_id") != run["candidate"].get("candidate_id")):
            problems.append("verification_candidate_identity")
        try:
            validate_supersedes(run, runs)
            validate_reuse(run, runs, candidate_api)
            validate_direct(run)
            if run.get("candidate") is not None:
                historical_meta = dict(meta, candidate_tracking={"mode": "enabled"})
                validate_provenance(run, historical_meta, events[:position], run["candidate"]["identity"]["checkout_realpath"], candidate_api, check_files=False)
        except (EvidenceError, TypeError, KeyError, ValueError) as exc:
            problems.append("verification_evidence: " + str(exc))
        runs.append(run)
    if starts != build_starts(meta):
        problems.append("build_start_event_mismatch")
    return problems
