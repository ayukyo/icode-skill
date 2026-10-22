"""Sequential acceptance gates over ordinary tickets and their existing ledger.

The locator is only a reference. Protected metadata, mirrored by metadata_updated
events, is the authority. Read guards never acquire a second ticket lock. Writers
take coordinator then child; no model, visual provider or device runs under locks.
"""
import copy
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import time
import uuid

_CONTROL = None
READ_ONLY = {"status", "trace", "plan", "check", "inspect", "init", "log"}
CODE_ACTIONS = {"code", "patch", "fast"}
PROTECTED = ("stage_program", "stage_binding", "serial_claims", "visual_reviews", "evidence_reviews")
DISCOVERY_LIMITS = {"max_entries": 4096, "max_files": 512, "max_events": 32768,
                    # One control file may use the existing cumulative budget;
                    # max_total_bytes still bounds every scanned ticket together.
                    "max_file_bytes": 64 * 1024 * 1024, "max_total_bytes": 64 * 1024 * 1024,
                    "timeout_seconds": 5}


def ctl():
    global _CONTROL
    if _CONTROL is None:
        spec = importlib.util.spec_from_file_location("stage_control_reader", Path(__file__).with_name("icode_control.py"))
        _CONTROL = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_CONTROL)
    return _CONTROL


def fail(message, **details):
    raise ctl().ControlError("stage: " + message, gate_id="stage_gate", **details)


def digest(value):
    return ctl().canonical_digest(value)


def locator_path(project):
    return Path(project) / ".icode_output" / ".stage_program" / "locator.json"


def discover_stage_history(project, *, limits=None, tolerate=None):
    """Bounded control-file discovery after complete locator-directory loss.

    Only fixed ordinary/debug ticket directories are inspected. A metadata
    marker is trusted only with its writer-only event mirror and intact ledger.
    No Git query or historical raw evidence read is performed. Time is checked
    around local I/O; an OS filesystem call itself is not interruptible here.

    ``tolerate`` names the one ordinary ticket whose own interrupted or damaged
    control state is being handled by its business lane; it can never hold a
    stage marker, so its state proves nothing about stage history.
    """
    api, project = ctl(), Path(project).resolve()
    tolerate = Path(tolerate).resolve() if tolerate is not None else None
    bounds = dict(DISCOVERY_LIMITS)
    if limits is not None:
        if not isinstance(limits, dict) or set(limits) - bounds.keys() or any(
                type(value) is not int or not 0 < value <= bounds[key] for key, value in limits.items()):
            fail("invalid stage discovery budget")
        bounds.update(limits)
    started = time.monotonic()
    counts = {"entries": 0, "files": 0, "bytes": 0, "events": 0}
    def check():
        if time.monotonic() - started > bounds["timeout_seconds"]:
            fail("stage history discovery time budget exhausted; locator state is unknown")
    def identity(info, *, cross_source=False):
        value = info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns
        # Windows path/handle ctime differs; same-source checks retain it below.
        return value if cross_source and os.name == "nt" else value + (info.st_ctime_ns,)
    def ordinary(path, directory=False):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 \
                or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            fail("stage discovery refuses a symlink/reparse or nonordinary control path")
        return info
    def read(path):
        check()
        counts["files"] += 1
        if counts["files"] > bounds["max_files"]:
            fail("stage history discovery file budget exhausted")
        before = ordinary(path)
        parent = ordinary(path.parent, True)
        if before.st_size > bounds["max_file_bytes"] or counts["bytes"] + before.st_size > bounds["max_total_bytes"]:
            fail("stage history discovery byte budget exhausted")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as handle:
            opened = os.fstat(handle.fileno())
            if identity(opened, cross_source=True) != identity(before, cross_source=True):
                fail("stage discovery control file replaced before reading")
            raw = handle.read(min(bounds["max_file_bytes"], bounds["max_total_bytes"] - counts["bytes"]) + 1)
            if identity(os.fstat(handle.fileno())) != identity(opened):
                fail("stage discovery control file changed while reading")
        if len(raw) != before.st_size or identity(path.lstat()) != identity(before) \
                or (parent.st_dev, parent.st_ino, parent.st_mode) != tuple(getattr(path.parent.lstat(), key) for key in ("st_dev", "st_ino", "st_mode")):
            fail("stage discovery control file/parent changed while reading")
        counts["bytes"] += len(raw)
        check()
        return raw
    def tickets(root):
        ordinary(root, True)
        with os.scandir(root) as entries:
            for entry in entries:
                check()
                counts["entries"] += 1
                if counts["entries"] > bounds["max_entries"]:
                    fail("stage history discovery directory budget exhausted")
                if entry.name.startswith(".icode_output_") and entry.name.removeprefix(".icode_output_").isdigit():
                    path = root / entry.name
                    ordinary(path, True)
                    yield path
    output = project / ".icode_output"
    try:
        if not os.path.lexists(output):
            return None
        roots = [output]
        if os.path.lexists(output / ".debug"):
            roots.append(output / ".debug")
        event_schema = api.load_json(api.SCHEMA_EVENT, "stage discovery event schema")
        for root in roots:
            for directory in tickets(root):
                meta_path, events_path = directory / api.METADATA_NAME, directory / api.EVENTS_NAME
                if not os.path.lexists(meta_path):
                    if os.path.lexists(events_path) or os.path.lexists(directory / api.TXN_NAME):
                        fail("stage discovery found an orphan ledger; locator state is unknown")
                    continue
                meta = api.strict_json_loads(read(meta_path).decode("utf-8"))
                if not isinstance(meta, dict):
                    fail("stage discovery metadata is not an object")
                markers = protected_values(meta)
                has_marker = any(markers[key] is not None for key in ("stage_program", "stage_binding"))
                tolerant = tolerate is not None and directory == tolerate and not has_marker
                if os.path.lexists(directory / api.TXN_NAME):
                    if tolerant:
                        continue
                    fail("stage discovery found a pending transaction; recover its control ledger first")
                if not os.path.lexists(events_path):
                    if tolerant:
                        continue
                    if api.is_vnext(meta) or has_marker:
                        fail("stage discovery cannot authenticate metadata without its ledger")
                    continue  # Ordinary legacy tickets had no vNext ledger.
                unproven = {
                    "stage: stage discovery control ledger hash/schema/identity is damaged",
                    "stage: stage discovery control ledger has no authenticated origin",
                    "stage: stage discovery metadata/protected writer ledger mirrors disagree",
                }
                try:
                    events, previous, seen = [], api.GENESIS_HASH, set()
                    for line in read(events_path).splitlines():
                        if not line.strip():
                            continue
                        check()
                        counts["events"] += 1
                        if counts["events"] > bounds["max_events"]:
                            fail("stage history discovery event budget exhausted")
                        event = api.strict_json_loads(line.decode("utf-8"))
                        if api.check_schema(event, event_schema) or event["event_id"] in seen \
                                or (events and event.get("ticket_id") != events[0].get("ticket_id")) or event["previous_event_hash"] != previous \
                                or event["event_hash"] != api.canonical_event_hash(event):
                            fail("stage discovery control ledger hash/schema/identity is damaged")
                        seen.add(event["event_id"])
                        previous = event["event_hash"]
                        events.append(event)
                    if not events or events[0]["event_type"] not in {"ticket_created", "migration_applied"}:
                        fail("stage discovery control ledger has no authenticated origin")
                    if event_issues(events, meta):
                        fail("stage discovery metadata/protected writer ledger mirrors disagree")
                except api.ControlError as exc:
                    # The tolerated ordinary ticket's ledger is the business
                    # lane's contract, not stage evidence. Budget, timeout,
                    # symlink and repeated-read failures stay fatal.
                    if not tolerant or str(exc) not in unproven:
                        raise
                    continue
                stage_history = has_marker or any((event.get("payload") or {}).get("stage_control") is not None for event in events)
                if stage_history:
                    # Discovery is a stage guard, not a cross-ticket audit of
                    # unrelated generic metrics or source-path migrations.
                    api.require_valid_metadata(meta)
                    if events[0].get("ticket_id") != meta.get("ticket_id"):
                        fail("stage discovery ticket/ledger identity mismatch")
                    if _project(meta) != project:
                        fail("stage discovery ticket project identity mismatch")
                    hashed = [event for event in events if (event.get("payload") or {}).get("metadata_hash_after")]
                    if hashed and hashed[-1]["payload"]["metadata_hash_after"] != api.metadata_hash(meta):
                        fail("stage discovery metadata/protected writer ledger mirrors disagree")
                check()
                if stage_history:
                    return {"control_root": str(directory), "reason": "authenticated_stage_history"}
        return None
    except (api.ControlError,):
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        fail("stage history discovery cannot establish ordinary-project state: " + type(exc).__name__)


def protected_values(meta):
    ext = meta.get("extensions") or {}
    ver = ext.get("verification") or {}
    return {"stage_program": ext.get("stage_program"), "stage_binding": ext.get("stage_binding"),
            "serial_claims": ver.get("serial_claims"), "visual_reviews": ver.get("visual_reviews"),
            "evidence_reviews": ver.get("evidence_reviews")}


def protect_update(before, after):
    if protected_values(before) != protected_values(after):
        fail("protected namespaces require their dedicated writer")
    binding = (before.get("extensions") or {}).get("stage_binding")
    receipts = protected_values(before)
    if binding is None or not (before.get("verification_runs") or receipts["evidence_reviews"] or receipts["visual_reviews"]):
        return
    require_contract_strength(before.get("verification_contract") or {}, after.get("verification_contract") or {})


def require_contract_strength(old, new):
    """Shared monotone contract comparison; ordinary update eligibility is unchanged."""
    if old == new:
        return
    if old.get("required") is True and new.get("required") is not True:
        fail("frozen verification requirements cannot be disabled; explicitly replan")
    previous, proposed = old.get("evidence_contract") or {}, new.get("evidence_contract") or {}
    proposed_rows = {row["id"]: row for row in proposed.get("required_items") or []}
    for row in previous.get("required_items") or []:
        later = proposed_rows.get(row["id"])
        if later is None or any(row[key] != later.get(key) for key in ("method", "kind", "layer", "consumer", "scenario")) \
                or later.get("min_count", 0) < row["min_count"] or (row["complete"] and later.get("complete") is not True):
            fail("frozen evidence method/scope cannot be deleted or weakened after observation; explicitly replan")
        if row.get("expected_mode") is not None and later.get("expected_mode") != row["expected_mode"]:
            fail("frozen expected_mode cannot be changed or deleted after observation; explicitly replan")
    if not set(previous.get("required_identity") or []).issubset(proposed.get("required_identity") or []):
        fail("frozen required identities cannot be removed")
    for key in ("required_layers", "required_consumers", "required_scenarios"):
        if not set(old.get(key) or []).issubset(new.get(key) or []):
            fail("frozen verification cells cannot be removed")
    old_cells = ctl().verification_evidence_module().required_cells(old) or []
    new_cells = ctl().verification_evidence_module().required_cells(new) or []
    if not set(old_cells).issubset(new_cells):
        fail("frozen required verification cells cannot be weakened")
    if previous.get("visual") is not None:
        a, b = previous["visual"], proposed.get("visual")
        if not isinstance(b, dict) or b.get("required") is not True or b.get("min_frames", 0) < a["min_frames"] \
                or b.get("sample_fractions") != a["sample_fractions"] or b.get("tolerance_fraction", 1) > a["tolerance_fraction"] \
                or (a["require_display"] and b.get("require_display") is not True):
            fail("frozen visual coverage cannot be weakened")
    metric_keys = ("name", "layer", "consumer", "scenario")
    metrics = {tuple(row[k] for k in metric_keys): row for row in new.get("required_metrics") or []}
    for row in old.get("required_metrics") or []:
        later = metrics.get(tuple(row[k] for k in metric_keys))
        if later is None or any(row[k] != later.get(k) for k in ("operator", "unit")):
            fail("frozen required metric cannot be removed or reinterpreted")
        op = row["operator"]
        strict = later["value"] >= row["value"] if op in {"gt", "gte"} else later["value"] <= row["value"] if op in {"lt", "lte"} else later["value"] == row["value"]
        if not strict:
            fail("frozen required metric threshold cannot be weakened")


def event_issues(events, meta):
    """Validate protected mirrors structurally, without rereading old raw files."""
    mirror = dict.fromkeys(PROTECTED)
    issues = []
    for event in events:
        payload = event.get("payload") or {}
        marker = payload.get("stage_control")
        serial = payload.get("serial_claim_control")
        visual = payload.get("visual_review_control")
        generic = payload.get("evidence_review_control")
        if event.get("event_type") in {"ticket_created", "migration_applied"}:
            if any(value is not None for value in protected_values(payload).values()):
                issues.append("stage_protected_birth")
        if event.get("event_type") != "metadata_updated":
            if any(x is not None for x in (marker, serial, visual, generic)):
                issues.append("stage_marker_event_type")
            continue
        updates = payload.get("set") or {}
        values = protected_values(updates)
        changed = []
        if "extensions" in updates:
            changed = [key for key in PROTECTED if values[key] != mirror[key]]
        expected = []
        if marker is not None:
            namespace = payload.get("stage_namespace")
            if marker != 1 or type(marker) is not int or namespace not in {"stage_program", "stage_binding"} \
                    or payload.get("stage_action") not in {"register", "bind", "review", "decision", "revise"}:
                issues.append("stage_control_shape")
            else:
                expected = [namespace]
                if namespace == "stage_program" and mirror[namespace] is not None:
                    old_program, new_program = mirror[namespace], values[namespace]
                    if payload.get("stage_action") == "revise":
                        try:
                            _validate_revision_append(old_program, new_program)
                        except (ctl().ControlError, ValueError, TypeError, KeyError):
                            issues.append("stage_revision_not_append_only")
                    elif any(old_program.get(key) != new_program.get(key)
                             for key in ("plan", "plan_digest", "plan_revisions")):
                        issues.append("stage_plan_changed_without_revision")
        elif serial is not None:
            if serial != 1 or type(serial) is not int:
                issues.append("serial_claim_control_shape")
            expected = ["serial_claims"]
            old, new = mirror["serial_claims"] or [], values["serial_claims"]
            if not isinstance(new, list) or len(new) != len(old) + 1 or new[:-1] != old \
                    or not isinstance(payload.get("serial_claim"), dict) or new[-1] != payload["serial_claim"]:
                issues.append("serial_claim_append_only")
        elif visual is not None:
            if visual != 1 or type(visual) is not int:
                issues.append("visual_review_control_shape")
            expected = ["visual_reviews"]
            old, new = mirror["visual_reviews"] or [], values["visual_reviews"]
            if not isinstance(new, list) or len(new) != len(old) + 1 or new[:-1] != old \
                    or not isinstance(payload.get("visual_review"), dict) or new[-1] != payload["visual_review"]:
                issues.append("visual_review_append_only")
        elif generic is not None:
            if generic != 1 or type(generic) is not int:
                issues.append("evidence_review_control_shape")
            expected = ["evidence_reviews"]
            old, new = mirror["evidence_reviews"] or [], values["evidence_reviews"]
            if not isinstance(new, list) or len(new) != len(old) + 1 or new[:-1] != old \
                    or not isinstance(payload.get("evidence_review"), dict) or new[-1] != payload["evidence_review"]:
                issues.append("evidence_review_append_only")
        if expected and (event.get("actor") != "icode" or not event.get("request_id") or sorted(changed) != expected):
            issues.append("stage_writer_marker_required")
        if changed and not expected:
            issues.append("stage_protected_writer_bypassed")
        if marker is not None and expected and values.get(expected[0]) != payload.get("stage_value"):
            issues.append("stage_event_value_mismatch")
        if "extensions" in updates:
            mirror = values
    if mirror != protected_values(meta):
        issues.append("stage_metadata_event_mismatch")
    return issues


def _access(provider=None, required=False):
    return {"git_provider": provider, "require_git_provider": required} if ctl()._candidate_access_requested(provider, required) else {}


def _verified(directory, *, _metadata_override=None, _allow_pending=False, _observation=None, **access):
    api = ctl()
    directory = Path(directory).resolve(strict=True)
    if _observation is not None:
        if type(_observation) is not api._ControlReadObservation or _metadata_override is not None or _allow_pending:
            fail("observed predecessor requires actual nonpending control reads")
        meta = api.load_metadata(directory, _observation=_observation)
    else:
        meta = api.load_metadata(directory) if _metadata_override is None else _metadata_override
    api.require_vnext(meta, directory)
    api.require_valid_metadata(meta)
    if (directory / api.TXN_NAME).exists():
        if not _allow_pending or _metadata_override is None or api.load_json(directory / api.TXN_NAME, "stage publication transaction").get("before_metadata") != meta:
            fail("pending transaction requires controlled recovery", directory=str(directory))
    events, issues = api.verify_event_chain(directory, meta, **access) if _observation is None else \
        api.verify_event_chain(directory, meta, _observation=_observation, **access)
    if issues:
        fail("event chain invalid", violations=issues)
    if _observation is not None:
        _observation.bind(directory, meta, events)
    return meta, events


def _project(meta):
    return Path(meta["project_path"]).resolve()


def _resolve(directory, *, _metadata_override=None, _allow_pending=False, **access):
    directory = Path(directory).resolve()
    hint = ctl().load_metadata(directory) if _metadata_override is None else _metadata_override
    if not locator_path(_project(hint)).parent.exists() and not any(protected_values(hint)[key] is not None for key in ("stage_binding", "stage_program")):
        if discover_stage_history(_project(hint), tolerate=directory) is not None:
            fail("project stage locator directory is missing; recover it with stage register on the authenticated coordinator")
        # No stage state to project. Provider context belongs to the business
        # lane; failing it here would replace its own gate with stage_gate.
        return None
    meta, events = _verified(directory, _metadata_override=_metadata_override, _allow_pending=_allow_pending, **access)
    binding = (meta.get("extensions") or {}).get("stage_binding")
    own_program = (meta.get("extensions") or {}).get("stage_program")
    project = _project(meta)
    locator = locator_path(project)
    if not locator.exists():
        if locator.parent.exists() or binding is not None or own_program is not None:
            fail("enabled project locator is missing")
        return None
    try:
        ref = ctl().load_json(locator, "stage locator")
        coordinator = Path(ref["coordinator_root"]).resolve(strict=True)
        coord_meta, coord_events = (meta, events) if coordinator == directory else _verified(coordinator, **access)
        program = (coord_meta.get("extensions") or {})["stage_program"]
        plans = _revision_plans(program)
        if ref != {"schema_version": 1, "coordinator_root": str(coordinator),
                   "program_id": program["plan"]["program_id"], "plan_digest": digest(plans[0])} \
                or Path(program["plan"]["project_root"]).resolve() != project \
                or _project(coord_meta) != project or digest(program["plan"]) != program["plan_digest"]:
            fail("locator/program identity mismatch")
        if not isinstance(program.get("generation"), int) or program["generation"] < 1:
            fail("invalid program generation")
        sid = None
        if binding is not None:
            sid = binding["stage_id"]
            expected = {"version": 1, "program_id": program["plan"]["program_id"], "stage_id": sid,
                        "coordinator_root": str(coordinator), "plan_digest": binding.get("plan_digest")}
            if binding != expected or binding.get("plan_digest") not in {digest(plan) for plan in plans} \
                    or sid not in {row["id"] for plan in plans for row in plan["stages"]} \
                    or program["bindings"].get(sid) != {"control_root": str(directory), "ticket_id": meta["ticket_id"]}:
                fail("stage binding is missing or disagrees with coordinator")
        elif directory != coordinator:
            fail("enabled project ticket is not bound to a planned stage")
        return {"directory": directory, "meta": meta, "events": events, "coordinator": coordinator,
                "coord_meta": coord_meta, "coord_events": coord_events, "program": program, "stage_id": sid}
    except (KeyError, TypeError, ValueError, OSError) as exc:
        if isinstance(exc, ctl().ControlError):
            raise
        fail("invalid stage locator or binding: " + str(exc))


def _write(directory, before, namespace, value, action, request_id, *, before_append=None, **access):
    if not isinstance(request_id, str) or not request_id.strip():
        fail("nonempty request_id required")
    after = copy.deepcopy(before)
    after.setdefault("extensions", {})[namespace] = value
    payload = {"stage_control": 1, "stage_action": action, "stage_namespace": namespace,
               "stage_value": value, "set": {"extensions": after["extensions"]}, "append": {}}
    events, _issues = ctl().read_events(directory)
    prior = ctl().find_idempotent_event(events, request_id, "metadata_updated", payload,
        actor="icode", payload_keys=("stage_control", "stage_action", "stage_namespace", "stage_value", "set", "append"))
    if prior is not None:
        return prior
    publication = dict(access)
    if access:
        publication["before_publish"] = lambda: ctl()._candidate_context_guard(directory, before, access["git_provider"], access["require_git_provider"])
    return ctl().commit_metadata_and_event(directory, before, after, "metadata_updated", payload,
                                           request_id=request_id, before_append=before_append, **publication)


def register_program(directory, plan, *, request_id, git_provider=None, require_git_provider=False):
    api = ctl()
    directory = Path(directory).resolve()
    access = _access(git_provider, require_git_provider)
    api.validate_against(plan, api.SCHEMA_DIR / "stage-program.schema.json", "stage_plan_schema")
    ids = [row["id"] for row in plan["stages"]]
    if len(set(ids)) != len(ids) or len(set(plan["authorized_actions"])) != len(plan["authorized_actions"]):
        fail("stage IDs and authorized actions must be unique")
    if any(not set(row["required_actions"]).issubset(plan["authorized_actions"]) or
           len(set(row["required_actions"])) != len(row["required_actions"]) for row in plan["stages"]):
        fail("stage actions exceed authorized plan scope")
    project = Path(plan["project_root"]).resolve()
    if project != _project(api.load_metadata(directory)):
        fail("plan project must match coordinator project")
    registration = locator_path(project).parent
    registration.mkdir(parents=True, exist_ok=True)
    # Different coordinator tickets must not race to replace the one locator.
    with api.FileLock(registration / ".registration.lock"), api.DirLock(directory):
        api.recover_pending_transaction(directory)
        meta, _events = _verified(directory, **access)
        if Path(plan["project_root"]).resolve() != _project(meta):
            fail("plan project must match coordinator project")
        # Persist enable intent before the program transaction. A crash between
        # metadata and locator publication must never reopen ordinary creation.
        path = locator_path(_project(meta))
        path.parent.mkdir(parents=True, exist_ok=True)
        old = (meta.get("extensions") or {}).get("stage_program")
        if old is not None:
            if old["plan"] != plan:
                fail("registered plan is immutable; use a new program")
            program = old
        else:
            if locator_path(_project(meta)).exists():
                fail("project already references another program")
            program = {"version": 1, "generation": 1, "plan": copy.deepcopy(plan), "plan_digest": digest(plan),
                       "bindings": {}, "reviews": {}, "decisions": []}
            _write(directory, meta, "stage_program", program, "register", request_id, **access)
        ref = {"schema_version": 1, "coordinator_root": str(directory), "program_id": plan["program_id"],
               "plan_digest": digest(_revision_plans(program)[0])}
        path = locator_path(_project(meta))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and api.load_json(path, "stage locator") != ref:
            fail("existing locator disagrees; refusing replacement")
        api.atomic_write_json(path, ref)
    return {"ok": True, "program_id": plan["program_id"], "generation": program["generation"]}


def _check_revision_plan(previous, proposed, accepted_prefix):
    """Retain accepted stages and monotonically strengthen pending obligations."""
    violations = ctl().validate_against(proposed, ctl().SCHEMA_DIR / "stage-program.schema.json", "stage_plan_schema")
    if violations:
        fail("invalid revision plan schema", violations=violations)
    if any(previous[key] != proposed[key] for key in ("schema_version", "program_id", "project_root")):
        fail("revision cannot change program/project identity")
    if set(previous["authorized_actions"]) != set(proposed["authorized_actions"]) \
            or len(set(proposed["authorized_actions"])) != len(proposed["authorized_actions"]):
        fail("revision cannot expand or remove authorized actions")
    rows = {row["id"]: row for row in proposed["stages"]}
    if len(rows) != len(proposed["stages"]):
        fail("revision stage IDs must be unique")
    for row in proposed["stages"]:
        actions = row["required_actions"]
        if len(actions) != len(set(actions)) or not set(actions).issubset(proposed["authorized_actions"]):
            fail("revision stage actions exceed authorized scope")
    for old in previous["stages"]:
        new = rows.get(old["id"])
        if new is None:
            fail("revision cannot remove an existing stage")
        if old["id"] in accepted_prefix:
            if new != old:
                fail("revision cannot modify an accepted stage")
        else:
            # Only additive required actions are allowed. Preserve every other
            # field and the relative order of all original obligations.
            original_actions = old["required_actions"]
            if dict(new, required_actions=original_actions) != old or \
                    [action for action in new["required_actions"] if action in original_actions] != original_actions:
                fail("revision cannot weaken or rewrite an existing stage")
    old_ids = [row["id"] for row in previous["stages"]]
    new_ids = [row["id"] for row in proposed["stages"]]
    if old_ids[:len(accepted_prefix)] != accepted_prefix or new_ids[:len(accepted_prefix)] != accepted_prefix:
        fail("revision cannot move accepted prefix or insert a predecessor before it")


_CONSOLIDATION = None


def consolidation_module():
    global _CONSOLIDATION
    if _CONSOLIDATION is None:
        spec = importlib.util.spec_from_file_location("icode_stage_consolidation", Path(__file__).with_name("stage_consolidation.py"))
        _CONSOLIDATION = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_CONSOLIDATION)
    return _CONSOLIDATION


def require_inherited_contract(state, metadata):
    if state is not None and state.get("stage_id") is not None:
        consolidation_module().require_contract(ctl(), state["program"], state["stage_id"], metadata.get("verification_contract") or {})


def _revision_plans(program):
    """The locator remains an immutable registration anchor; history links it to current plan."""
    history = program.get("plan_revisions", [])
    if not isinstance(history, list):
        fail("invalid revision history")
    plans = [row["previous_plan"] for row in history] + [program["plan"]]
    seen, generation, retired = set(), 0, set()
    for index, row in enumerate(history):
        if row["request_id"] in seen or type(row["generation"]) is not int \
                or row["generation"] <= generation or row["generation"] > program["generation"]:
            fail("invalid revision identity/generation")
        seen.add(row["request_id"])
        generation = row["generation"]
        if digest(plans[index]) != row["old_plan_digest"] or digest(plans[index + 1]) != row["new_plan_digest"]:
            fail("broken plan revision lineage")
        snapshot = row.get("consolidation")
        if snapshot is None:
            _check_revision_plan(plans[index], plans[index + 1], row["accepted_prefix"])
            inserted = {stage["id"] for stage in plans[index + 1]["stages"]} - {stage["id"] for stage in plans[index]["stages"]}
            if inserted & retired:
                fail("ordinary revision cannot reuse a retired stage ID")
        else:
            helper = consolidation_module()
            helper.validate_snapshot(ctl(), snapshot, plans[index], plans[index + 1], row["accepted_prefix"], retired=retired)
            retired.update(mapping["source_stage_id"] for mapping in snapshot["descriptor"]["mappings"])
    return plans


def _validate_revision_append(previous, proposed):
    history = proposed.get("plan_revisions")
    old = previous.get("plan_revisions", [])
    if not isinstance(history, list) or len(history) != len(old) + 1 or history[:-1] != old:
        fail("revision history must be append-only")
    row = history[-1]
    if row["previous_plan"] != previous["plan"] or row["old_plan_digest"] != previous["plan_digest"] \
            or row["new_plan_digest"] != proposed["plan_digest"] \
            or row["generation"] != previous["generation"] + 1 or proposed["generation"] != row["generation"]:
        fail("revision does not extend the actual previous program")
    invalidated = [review["review_id"] for sid, rows in previous["reviews"].items()
                   if sid not in row["accepted_prefix"] for review in rows]
    recorded = row.get("invalidated_reviews")
    # JSON object-key serialization can reorder review stages; these IDs name
    # invalidated members, while the original review arrays remain protected.
    if not isinstance(recorded, list) or len(recorded) != len(invalidated) \
            or any(not isinstance(review_id, str) for review_id in recorded) \
            or len(set(recorded)) != len(recorded) or sorted(recorded) != sorted(invalidated):
        fail("revision must invalidate pending reviews while retaining their evidence")
    # Every previous protected decision and observation remains byte-for-byte identical.
    expected = copy.deepcopy(previous)
    expected.update(plan=proposed["plan"], plan_digest=proposed["plan_digest"],
                    generation=proposed["generation"], plan_revisions=history)
    if expected != proposed:
        fail("revision changed bindings/reviews/decisions or another program field")
    _revision_plans(proposed)


def _revision_preview(state, proposed, *, consolidation=None, **access):
    api = ctl()
    program = state["program"]
    accepted, gap = [], False
    for row in program["plan"]["stages"]:
        decision = _decision(program, row["id"])
        confirmed = bool(decision and decision["decision"] == "confirm")
        if confirmed and gap:
            fail("revision supports only an intact accepted prefix")
        if confirmed:
            accepted.append(row["id"])
        else:
            gap = True
    snapshot = None
    if consolidation is None:
        _check_revision_plan(program["plan"], proposed, accepted)
        if ({row["id"] for row in proposed["stages"]} - {row["id"] for row in program["plan"]["stages"]}) & consolidation_module().retired_ids(program):
            fail("ordinary revision cannot reuse a retired stage ID")
    else:
        snapshot = consolidation_module().build_snapshot(api, program, proposed, accepted, consolidation, **access)
    if proposed == program["plan"]:
        fail("revision must contain an actual plan change")
    probe = dict(state, stage_id=next((row["id"] for row in program["plan"]["stages"]
                                     if row["id"] not in accepted), None))
    _refs, reasons = _predecessors(probe, **access)
    if reasons:
        fail("accepted prefix evidence is invalid", reasons=reasons)
    facts = []
    roots = [(state["coordinator"], state["coord_meta"], state["coord_events"])]
    for sid, ref in sorted(program["bindings"].items()):
        child_root = Path(ref["control_root"]).resolve()
        child_meta, child_events = _verified(child_root, **access)
        binding = (child_meta.get("extensions") or {}).get("stage_binding") or {}
        expected = {"version": 1, "program_id": program["plan"]["program_id"], "stage_id": sid,
                    "coordinator_root": str(state["coordinator"]), "plan_digest": binding.get("plan_digest")}
        if binding != expected or binding.get("plan_digest") not in {digest(plan) for plan in _revision_plans(program)} \
                or child_meta["ticket_id"] != ref["ticket_id"] or _project(child_meta) != _project(state["coord_meta"]):
            fail("revision requires intact bound tickets")
        # Moving an unaccepted stage cannot hide damaged historical reports.
        for review in program["reviews"].get(sid, []):
            try:
                _check_report(child_root, review)
            except (ValueError, OSError) as exc:
                fail("historical report reference is invalid", stage_id=sid, reason=str(exc))
        roots.append((child_root, child_meta, child_events))
    for root, meta, events in roots:
        mirror = api.ExecutionBindingMirror(meta)
        for event in events:
            mirror.consume(event)
        if meta.get("close_state") is not None or mirror.open_steps or mirror.open_operations or mirror.open_agents:
            fail("revision requires quiescent, open control tickets", directory=str(root))
        facts.append({"root": str(root), "metadata": api.metadata_hash(meta), "tail": events[-1]["event_hash"]})
    view = state.get("_publishing_projection") or projection(state["coordinator"], **access)
    if view["blocked"]:
        fail("revision coordinator is invalid", stage=view)
    consolidation_snapshot = snapshot
    snapshot = {"program_id": program["plan"]["program_id"], "generation": program["generation"],
                "revision": view["revision"], "old_plan_digest": program["plan_digest"],
                "new_plan_digest": digest(proposed), "controls": facts, "accepted_prefix": accepted}
    if consolidation_snapshot is not None:
        snapshot["consolidation"] = consolidation_snapshot
    old_ids = [row["id"] for row in program["plan"]["stages"]]
    new_ids = [row["id"] for row in proposed["stages"]]
    def eligible(order, sid):
        return sid not in accepted and all(prior in accepted for prior in order[:order.index(sid)])
    old_actions = {row["id"]: row["required_actions"] for row in program["plan"]["stages"]}
    action_effects = {row["id"]: {"required_actions": row["required_actions"],
        "added_actions": [action for action in row["required_actions"] if action not in old_actions.get(row["id"], [])],
        "eligible_before_revision": row["id"] in old_ids and eligible(old_ids, row["id"]),
        "eligible_after_revision": eligible(new_ids, row["id"]),
        "binding_required": row["id"] not in program["bindings"]} for row in proposed["stages"]}
    # A readonly preview can be exported as a complete original-record backup.
    backup = {"original_plan": program["plan"], "locator": api.load_json(locator_path(_project(state["coord_meta"])), "stage locator"),
              "controls": [{"root": str(root), "metadata": meta, "events": events} for root, meta, events in roots]}
    challenge = {key: snapshot[key] for key in ("program_id", "generation", "revision", "old_plan_digest", "new_plan_digest")}
    challenge["preview_digest"] = digest(snapshot)
    result = {"ok": True, "challenge": challenge, "accepted_prefix": accepted,
            "previous_order": old_ids, "proposed_order": new_ids,
            "inserted_stages": [sid for sid in new_ids if sid not in old_ids],
            "action_effects": action_effects, "backup_snapshot": backup, "backup_sha256": digest(backup),
            "preserved_reviews": {sid: [row["outcome"] for row in rows] for sid, rows in program["reviews"].items()},
            "note": "Preview is not execution/acceptance authorization; original obligations and decisions are preserved."}
    if consolidation_snapshot is not None:
        result["consolidation"] = consolidation_snapshot
    return result


def preview_revision(directory, proposed, *, consolidation=None, git_provider=None, require_git_provider=False):
    access = _access(git_provider, require_git_provider)
    state = _resolve(directory, **access)
    if state is None or state["stage_id"] is not None:
        fail("plan revision requires the registered coordinator")
    return _revision_preview(state, proposed, consolidation=consolidation, **access)


def revise_program(directory, proposed, request, *, trusted_confirmation=None,
                   consolidation=None, git_provider=None, require_git_provider=False):
    """Only a host/UI callback can attest approval; CLI JSON cannot."""
    if not callable(trusted_confirmation) or not isinstance(request, dict):
        fail("plan revision requires trusted host/UI user approval")
    required = {"program_id", "generation", "revision", "old_plan_digest", "new_plan_digest", "preview_digest", "request_id", "reason"}
    if set(request) != required or type(request["generation"]) is not int \
            or any(not isinstance(request[key], str) or not request[key].strip() for key in required - {"generation"}):
        fail("revision request must bind the exact preview and explicit reason/request_id")
    source = trusted_confirmation(copy.deepcopy(request))
    if not isinstance(source, dict) or source.get("source") not in {"ui", "host_user"} \
            or source.get("intent") != "修订阶段计划" or not isinstance(source.get("source_id"), str) or not source["source_id"].strip():
        fail("trusted callback did not attest explicit plan revision approval")
    access = _access(git_provider, require_git_provider)
    directory = Path(directory).resolve()
    request_identity = {"plan": proposed, "request": request}
    if consolidation is not None:
        request_identity["consolidation"] = consolidation
    request_digest = digest(request_identity)
    with ctl().DirLock(directory), contextlib.ExitStack() as child_locks:
        ctl().recover_pending_transaction(directory)
        state = _resolve(directory, **access)
        if state is None or state["stage_id"] is not None:
            fail("plan revision requires the registered coordinator")
        # Respect the established coordinator -> child writer lock order. A
        # generation check alone cannot close the final check/append window.
        child_roots = sorted({Path(ref["control_root"]).resolve() for ref in state["program"]["bindings"].values()}, key=str)
        if directory in child_roots:
            fail("coordinator cannot also be a bound child")
        for child_root in child_roots:
            child_locks.enter_context(ctl().DirLock(child_root))
        program = copy.deepcopy(state["program"])
        prior = next((row for row in program.get("plan_revisions", []) if row["request_id"] == request["request_id"]), None)
        if prior is not None:
            if prior["request_digest"] != request_digest or prior["source"] != source:
                fail("revision request replay payload/source conflict")
            return {"ok": True, "already_applied": True, "revision_id": prior["revision_id"], "generation": prior["generation"]}
        preview = _revision_preview(state, proposed, consolidation=consolidation, **access)
        if any(request[key] != value for key, value in preview["challenge"].items()):
            fail("plan revision preview is stale or does not match")
        row = {"revision_id": str(uuid.uuid4()), "request_id": request["request_id"],
               "request_digest": request_digest, "source": source, "at": ctl().now_iso(),
               "generation": program["generation"] + 1, "previous_plan": copy.deepcopy(program["plan"]),
               "old_plan_digest": program["plan_digest"], "new_plan_digest": digest(proposed),
               "accepted_prefix": preview["accepted_prefix"], "reason": request["reason"],
               "invalidated_reviews": [review["review_id"] for sid, rows in program["reviews"].items()
                                       if sid not in preview["accepted_prefix"] for review in rows],
               "preview_digest": request["preview_digest"]}
        if consolidation is not None:
            row["consolidation"] = preview["consolidation"]
        program.setdefault("plan_revisions", []).append(row)
        program.update(plan=copy.deepcopy(proposed), plan_digest=digest(proposed), generation=row["generation"])
        _validate_revision_append(state["program"], program)
        def publication_check():
            # The existing atomic writer has installed after-metadata but has
            # not appended its event. Read guards must still reject that gap;
            # only this publisher validates against its own recorded before state.
            transaction = ctl().load_json(state["coordinator"] / ctl().TXN_NAME, "revision transaction")
            actual = ctl().load_metadata(state["coordinator"])
            current_events, _issues = ctl().read_events(state["coordinator"])
            if transaction.get("before_metadata") != state["coord_meta"] \
                    or transaction.get("after_metadata") != actual \
                    or (actual.get("extensions") or {}).get("stage_program") != program \
                    or current_events[-1]["event_hash"] != state["coord_events"][-1]["event_hash"]:
                fail("revision publication transaction changed")
            publishing = dict(state, _publishing_projection={"blocked": False, "revision": preview["challenge"]["revision"]})
            if _revision_preview(publishing, proposed, consolidation=consolidation, **access)["challenge"] != preview["challenge"]:
                fail("plan revision facts changed before publication")
        _write(state["coordinator"], state["coord_meta"], "stage_program", program, "revise", request["request_id"],
               before_append=publication_check, **access)
    return {"ok": True, "already_applied": False, "revision_id": row["revision_id"], "generation": row["generation"]}


def bind_stage(coordinator, stage_id, directory, *, request_id, git_provider=None, require_git_provider=False):
    api = ctl()
    coordinator, directory = Path(coordinator).resolve(), Path(directory).resolve()
    if coordinator == directory:
        fail("coordinator cannot implement a stage")
    access = _access(git_provider, require_git_provider)
    # Fixed order is the only cross-ticket locking order.
    with api.DirLock(coordinator), api.DirLock(directory):
        return _bind_stage_locked(coordinator, stage_id, directory, request_id=request_id, **access)


def _bind_stage_locked(coordinator, stage_id, directory, *, request_id, git_provider=None, require_git_provider=False):
    """Internal birth/bind writer; caller holds coordinator then child locks."""
    api = ctl()
    coordinator, directory = Path(coordinator).resolve(), Path(directory).resolve()
    access = _access(git_provider, require_git_provider)
    api.recover_pending_transaction(coordinator)
    api.recover_pending_transaction(directory)
    state = _resolve(coordinator, **access)
    if state is None:
        fail("program must be registered before binding")
    program = copy.deepcopy(state["program"])
    if stage_id not in [row["id"] for row in program["plan"]["stages"]]:
        fail("unknown stage_id")
    meta, events = _verified(directory, **access)
    if _project(meta) != _project(state["coord_meta"]):
        fail("cross-project stage binding forbidden")
    binding = {"version": 1, "program_id": program["plan"]["program_id"], "stage_id": stage_id,
               "coordinator_root": str(coordinator), "plan_digest": program["plan_digest"]}
    existing = (meta.get("extensions") or {}).get("stage_binding")
    reference = {"control_root": str(directory), "ticket_id": meta["ticket_id"]}
    if existing is not None and existing.get("plan_digest") in {digest(plan) for plan in _revision_plans(program)}:
        binding["plan_digest"] = existing["plan_digest"]
    if existing is not None and existing != binding:
        fail("stage ticket binding is immutable")
    if stage_id in program["bindings"] and program["bindings"][stage_id] != reference:
        fail("stage already has another ticket")
    if any(row == reference and key != stage_id for key, row in program["bindings"].items()):
        fail("ticket cannot implement multiple stages")
    if existing is None:
        if any(e.get("event_type") in {"step_started", "operation_started", "verification_recorded"} for e in events):
            fail("cannot retroactively bind an already implemented ticket")
        consolidation_module().require_contract(api, program, stage_id, meta.get("verification_contract") or {})
        _write(directory, meta, "stage_binding", binding, "bind", request_id + ":child", **access)
    if program["bindings"].get(stage_id) != reference:
        program["bindings"][stage_id] = reference
        program["generation"] += 1
        _write(coordinator, state["coord_meta"], "stage_program", program, "bind", request_id, **access)
    return {"ok": True, "stage_id": stage_id, "generation": program["generation"]}


def review_validator(meta):
    namespace = ((meta.get("extensions") or {}).get("verification") or {})
    receipts = [*(namespace.get("visual_reviews") or []), *(namespace.get("evidence_reviews") or [])]
    def validate(ref):
        if not isinstance(ref, dict) or set(ref) != {"review_id"}:
            fail("structured trusted review reference required")
        rows = [row for row in receipts if row.get("review_id") == ref["review_id"]]
        if len(rows) != 1:
            fail("trusted review receipt missing or ambiguous")
        return copy.deepcopy(rows[0])
    return validate


def _latest_review(program, sid):
    rows = program["reviews"].get(sid) or []
    return rows[-1] if rows else None


def _decision(program, sid):
    rows = [row for row in program["decisions"] if row["stage_id"] == sid]
    return rows[-1] if rows else None


def _check_report(directory, review):
    reader = ctl().verification_evidence_module().bundle_module().Reader(directory)
    raw = reader.read(review["report_path"], "sha256:" + review["report_sha256"])
    source = reader.read(review["review_path"], "sha256:" + review["review_sha256"])
    reader.finish()
    if reader.json(source) != review["snapshot"] or review["facts_digest"] not in raw.decode("utf-8"):
        fail("review/report contents disagree with frozen snapshot")
    return raw.decode("utf-8")


def _negative_reviews(events, candidate_id, contract, qualified, *, after_position=None):
    """Actual negative calls cannot be hidden behind a previously qualified pass.

    A current stage may supersede a negative only with a newly qualified bundle
    referencing later successful calls. An accepted snapshot remains refuted
    until another explicit review/confirmation, so history uses after_position.
    """
    calls, reasons, failed = {}, [], False
    for position, event in enumerate(events):
        payload = event.get("payload") or {}
        for kind in ("visual_review", "evidence_review"):
            receipt = payload.get(kind) if payload.get(kind + "_control") == 1 else None
            if isinstance(receipt, dict):
                calls[receipt["review_id"]] = (position, kind, receipt)
    contract_id = ctl().verification_evidence_module().bundle_module().contract_digest(contract)
    requirements = ((contract or {}).get("evidence_contract") or {}).get("required_items") or []
    for identifier, (position, kind, receipt) in calls.items():
        if receipt.get("candidate_id") != candidate_id \
                or receipt.get("outcome") == "pass" or (after_position is not None and position <= after_position):
            continue
        if after_position is not None:
            if receipt.get("contract_digest") != contract_id:
                continue  # Accepted history retains its own frozen contract.
        else:
            # Strengthening coverage cannot erase an actual failure for the
            # same frozen requirement/cell. Only a later qualified bundle may
            # supersede it, even when that bundle uses a stronger contract.
            relevant = [row for row in requirements if
                        {key: row.get(key) for key in ("layer", "consumer", "scenario")} == receipt.get("cell")]
            if kind == "evidence_review":
                relevant = [row for row in relevant if row["id"] == receipt.get("requirement_id") and
                            row["method"] == receipt.get("method")]
            else:
                relevant = [row for row in relevant if row["kind"] in {"frame_set", "visual_review"}]
            if not relevant:
                continue
        superseded = False
        if after_position is None:
            for run, bundle in qualified:
                if {key: run.get(key) for key in ("layer", "consumer", "scenario")} != receipt.get("cell"):
                    continue
                if kind == "visual_review":
                    refs = [ref.get("review_id") for group in (bundle.get("visual") or {}).get("frame_sets") or []
                            for frame in group.get("frames") or [] for ref in frame.get("review_refs") or []]
                else:
                    refs = [item["metadata"]["review_ref"].get("review_id") for item in bundle["items"]
                            if item["requirement_id"] == receipt.get("requirement_id") and isinstance(item["metadata"].get("review_ref"), dict)]
                if refs and all(ref in calls and calls[ref][0] > position and calls[ref][2].get("outcome") == "pass" for ref in refs):
                    superseded = True
                    break
        if not superseded:
            reasons.append("actual_review_" + str(receipt.get("outcome")) + ":" + identifier)
            failed = failed or receipt.get("outcome") == "fail"
    return reasons, failed


def _historical(directory, review, *, _observation=None, **access):
    """Frozen accepted source is not compared against a later live candidate."""
    if _observation is not None:
        if type(_observation) is not ctl()._ControlReadObservation:
            fail("invalid internal predecessor observation")
        current, events = _observation.check(directory)
        if access:
            ctl()._candidate_context_guard(directory, current, access["git_provider"], access["require_git_provider"])
    else:
        current = ctl().load_metadata(directory)
        events, _ = ctl().read_events(directory)
    _check_report(directory, review)
    snapshot = review["snapshot"]
    by_id = {row.get("run_id"): row for row in current.get("verification_runs") or []}
    for ref in snapshot["facts"]["runs"]:
        if ref["run_id"] not in by_id or digest(by_id[ref["run_id"]]) != ref["digest"]:
            fail("accepted source verification event changed or disappeared")
    frozen = snapshot["evidence_metadata"]
    accepted_ids = {run["run_id"] for run in snapshot["accepted_runs"]}
    last_run_position = max((i for i, event in enumerate(events) if event.get("event_type") == "verification_recorded" and
                             (event.get("payload") or {}).get("run_id") in accepted_ids), default=-1)
    for run in snapshot["accepted_runs"]:
        ctl().verification_evidence_module().validate_bundle(run, frozen, directory,
                                                          review_validator=review_validator(frozen))
        negative, _failed = _negative_reviews(events, run.get("candidate_id"), frozen["verification_contract"], [], after_position=last_run_position)
        if negative:
            fail("accepted snapshot refuted by actual later review", violations=negative)
    if _observation is not None:
        _observation.check(directory)
        if access:
            ctl()._candidate_context_guard(directory, current, access["git_provider"], access["require_git_provider"])


def _predecessors(state, **access):
    program, sid = state["program"], state["stage_id"]
    refs, reasons = [], []
    for row in program["plan"]["stages"]:
        if row["id"] == sid:
            break
        prior_id = row["id"]
        decision = _decision(program, prior_id)
        prior = next((x for x in program["reviews"].get(prior_id, []) if decision and x["review_id"] == decision.get("review_id")), None)
        if not decision or decision["decision"] != "confirm" or prior is None:
            reasons.append("predecessor_not_accepted:" + prior_id)
            continue
        try:
            ref = program["bindings"][prior_id]
            observation = ctl()._ControlReadObservation(ref["control_root"])
            child, _events = _verified(ref["control_root"], _observation=observation, **access)
            bind = (child.get("extensions") or {}).get("stage_binding") or {}
            if child["ticket_id"] != ref["ticket_id"] or bind.get("program_id") != program["plan"]["program_id"] or bind.get("stage_id") != prior_id:
                fail("predecessor binding changed")
            _historical(ref["control_root"], prior, _observation=observation, **access)
            if prior["snapshot"]["facts"]["predecessors"] != refs:
                fail("accepted predecessor snapshot changed; later stage needs fresh acceptance")
            accepted_ids = {run["run_id"] for run in prior["snapshot"]["accepted_runs"]}
            last_position = max((i for i, run in enumerate(child.get("verification_runs") or []) if run["run_id"] in accepted_ids), default=-1)
            frozen_cells = {(run.get("candidate_id"), run.get("layer"), run.get("consumer"), run.get("scenario"))
                            for run in prior["snapshot"]["accepted_runs"]}
            if any(run.get("outcome") in {"fail", "inconclusive"} and
                   (run.get("candidate_id"), run.get("layer"), run.get("consumer"), run.get("scenario")) in frozen_cells
                   for run in (child.get("verification_runs") or [])[last_position + 1:]):
                fail("accepted observation contradicted by later verification")
            refs.append({"stage_id": prior_id, "facts_digest": prior["facts_digest"], "decision_id": decision["decision_id"]})
        except (ctl().ControlError, ValueError, TypeError, KeyError, OSError) as exc:
            reasons.append("predecessor_evidence_invalid:" + prior_id + ":" + str(exc))
    return refs, reasons


def _facts(state, **access):
    api = ctl()
    meta, directory, sid = state["meta"], state["directory"], state["stage_id"]
    program = state["program"]
    require_inherited_contract(state, meta)
    predecessor_refs, reasons = _predecessors(state, **access)
    contract = meta.get("verification_contract")
    stage = next(row for row in program["plan"]["stages"] if row["id"] == sid)
    current = None
    execution = api.execution_trace(state["events"])
    if any(execution.get(key) for key in ("open_steps", "open_operations", "open_agents")):
        reasons.append("open_execution_requires_actual_terminal_receipt")
    code_required = bool(set(stage["required_actions"]) & CODE_ACTIONS)
    if code_required and (meta.get("candidate_tracking") or {}).get("mode") != "enabled":
        reasons.append("candidate_tracking_required")
    if not isinstance(contract, dict) or contract.get("required") is not True or not api.verification_evidence_module().required_cells(contract) \
            or not isinstance(contract.get("evidence_contract"), dict) or not contract["evidence_contract"].get("required_items"):
        reasons.append("explicit_nonempty_verification_and_evidence_contract_required")
    try:
        context = api.verification_context(directory, meta, **access)
        context["review_validator"] = review_validator(meta)
        current = context.get("current")
        assessment = api.verification_evidence_module().assess(meta, context, **access)
        if assessment.get("ok") is not True or assessment.get("state") != "satisfied":
            reasons.extend(assessment.get("reasons") or ["verification_not_satisfied"])
        if code_required:
            freshness = api.delivery_freshness(directory, meta, **access)
            if freshness.get("ok") is not True:
                reasons.extend(freshness.get("reasons") or ["code_review_not_satisfied"])
        else:
            freshness = None
    except (api.ControlError, ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
        reasons.append("verification_unavailable:" + str(exc))
        assessment, freshness = {"ok": False}, None
    accepted = assessment.get("eligible_runs") or []
    # Snapshot keeps the exact run objects, including candidate/provenance.
    run_ids = {row.get("run_id") for row in accepted}
    runs = [copy.deepcopy(row) for row in meta.get("verification_runs", []) if row.get("run_id") in run_ids]
    qualified = []
    for run in runs:
        try:
            validated = api.verification_evidence_module().validate_bundle(run, meta, directory, review_validator=review_validator(meta))
            if validated is not None and run.get("outcome") == "pass":
                qualified.append((run, validated["bundle"]))
        except (ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
            reasons.append("evidence_invalid:" + str(exc))
    if not runs:
        reasons.append("required_verification_runs_missing")
    failed = any(row.get("outcome") == "fail" for row in accepted)
    relevant_candidates = {row.get("candidate_id") for row in runs}
    relevant_candidates.add(current.get("candidate_id") if current else None)
    for candidate_id in relevant_candidates:
        observation_reasons, observed_fail = _negative_reviews(state["events"], candidate_id, contract, qualified)
        reasons.extend(observation_reasons)
        failed = failed or observed_fail
    outcome = "pass" if not reasons else "fail" if failed else "inconclusive"
    current_summary = ({key: current.get(key) for key in ("candidate_id", "content_id", "base", "head", "head_tree")} if current else None)
    facts = {"plan": program["plan"], "stage_id": sid, "requirement": meta.get("requirement"),
             "acceptance_contract": meta.get("acceptance_contract"), "contract": contract, "current_candidate": current_summary,
             "baseline_ref": contract.get("baseline_ref") if isinstance(contract, dict) else None,
             "runs": [{"run_id": row.get("run_id"), "digest": digest(row), "outcome": row.get("outcome"),
                       "candidate_id": row.get("candidate_id"), "evidence_bundle": row.get("evidence_bundle")}
                      for row in meta.get("verification_runs") or []],
             "visual_reviews": [digest(row) for row in protected_values(meta)["visual_reviews"] or []],
             "evidence_reviews": [digest(row) for row in protected_values(meta)["evidence_reviews"] or []],
             "review_receipts_digest": digest(meta.get("review_receipts")), "predecessors": predecessor_refs,
             "assessment": assessment, "code_review": {"ok": freshness.get("ok"), "digest": digest(freshness)} if freshness else None}
    facts["execution_digest"] = digest([{key: event[key] for key in ("event_id", "event_type", "payload")}
        for event in state["events"] if event["event_type"] in {"step_started", "step_finished", "operation_started", "operation_finished", "agent_spawned", "agent_result"}])
    frozen = {"ticket_id": meta["ticket_id"], "verification_contract": copy.deepcopy(contract),
              "extensions": {"stage_binding": copy.deepcopy((meta.get("extensions") or {}).get("stage_binding")),
                             "verification": {key: copy.deepcopy(((meta.get("extensions") or {}).get("verification") or {}).get(key) or [])
                                              for key in ("visual_reviews", "evidence_reviews")}}}
    runs = [{key: value for key, value in run.items() if key not in {"candidate", "build_provenance", "deploy_provenance", "runtime_provenance"}}
            for run in runs]
    return {"facts": facts, "facts_digest": digest(facts), "outcome": outcome, "reasons": sorted(set(reasons)),
            "accepted_runs": runs, "evidence_metadata": frozen}


def _retirement_projection(directory, *, git_provider=None, require_git_provider=False,
                           _metadata_override=None, _allow_pending=False):
    """Authenticate binding/retirement only; grants no action or acceptance.

    Resolution errors propagate. Only neutral-write retirement guards use this
    view; actual action admission and ordinary publication still fully qualify.
    """
    state = _resolve(directory, _metadata_override=_metadata_override, _allow_pending=_allow_pending,
                     **_access(git_provider, require_git_provider))
    if state is None:
        return {"enabled": False}
    program, sid = state["program"], state["stage_id"]
    replacements = consolidation_module().replacements(program, sid) if sid is not None else None
    result = dict(enabled=True, stage_id=sid, program_id=program["plan"]["program_id"],
                  generation=program["generation"], coordinator_root=str(state["coordinator"]),
                  state="superseded" if replacements is not None else "in_progress")
    if replacements is not None:
        result["replacement_stages"] = replacements
    return result


def projection(directory, *, git_provider=None, require_git_provider=False, _metadata_override=None, _allow_pending=False):
    access = _access(git_provider, require_git_provider)
    result = {"enabled": False, "blocked": False, "state": "not_enabled", "reasons": [],
              "available_actions": [], "generation": None, "revision": None, "challenge": None, "report": None,
              "can_confirm": False, "can_revoke": False, "acceptance_snapshot": None}
    try:
        state = _resolve(directory, _metadata_override=_metadata_override, _allow_pending=_allow_pending, **access)
        if state is None:
            return result
        program, sid = state["program"], state["stage_id"]
        result.update(enabled=True, program_id=program["plan"]["program_id"], stage_id=sid,
                      generation=program["generation"], coordinator_root=str(state["coordinator"]), state="coordinator")
        if sid is None:
            result["stages"] = [{"stage_id": row["id"], "title": row["title"], "bound": row["id"] in program["bindings"],
                                 "decision": (_decision(program, row["id"]) or {}).get("decision")} for row in program["plan"]["stages"]]
            result["retired_stages"] = [{"stage_id": old, "replacement_stages": consolidation_module().replacements(program, old)}
                                        for old in sorted(consolidation_module().retired_ids(program))]
        elif consolidation_module().replacements(program, sid) is not None:
            result.update(state="superseded", replacement_stages=consolidation_module().replacements(program, sid))
            review = _latest_review(program, sid)
            if review is not None:
                text = _check_report(state["directory"], review)
                result["report"] = {"path": review["report_path"], "text": text[:131072], "outcome": review["outcome"],
                                    "facts_digest": review["facts_digest"], "report_sha256": review["report_sha256"]}
        else:
            require_inherited_contract(state, state["meta"])
            _refs, reasons = _predecessors(state, **access)
            review, decision = _latest_review(program, sid), _decision(program, sid)
            result.update(state="in_progress", blocked=bool(reasons), reasons=reasons)
            stage = next(row for row in program["plan"]["stages"] if row["id"] == sid)
            result["available_actions"] = [] if reasons else list(stage["required_actions"])
            if review is not None:
                if decision and decision["decision"] == "confirm" and decision.get("review_id") == review["review_id"]:
                    result["can_revoke"] = True
                    result["acceptance_snapshot"] = {"program_id": result["program_id"], "stage_id": sid,
                        "challenge_id": review["challenge_id"], "facts_digest": review["facts_digest"], "report_sha256": review["report_sha256"]}
                try:
                    text = _check_report(state["directory"], review)
                    result["report"] = {"path": review["report_path"], "text": text[:131072], "outcome": review["outcome"],
                                        "facts_digest": review["facts_digest"], "report_sha256": review["report_sha256"]}
                    if decision and decision.get("review_id") == review["review_id"]:
                        result["state"] = {"confirm": "accepted", "reject": "rejected", "revoke": "revoked"}[decision["decision"]]
                        if decision["decision"] == "confirm":
                            _historical(state["directory"], review)
                            if review["snapshot"]["facts"]["predecessors"] != _refs:
                                fail("predecessor acceptance snapshot changed")
                            result["available_actions"] = []
                    elif review["outcome"] == "pass" and not reasons:
                        facts = _facts(state, **access)
                        if facts["outcome"] == "pass" and facts["facts_digest"] == review["facts_digest"]:
                            result["state"] = "awaiting_confirmation"
                            result["can_confirm"] = True
                            result["challenge"] = {"challenge_id": review["challenge_id"], "facts_digest": review["facts_digest"],
                                                   "report_sha256": review["report_sha256"]}
                        else:
                            result.update(state="needs_review", reasons=["acceptance_facts_changed"], challenge=None)
                    else:
                        result["state"] = "needs_review"
                    if not (decision and decision["decision"] == "confirm" and decision.get("review_id") == review["review_id"]) \
                            and any(review["review_id"] in row.get("invalidated_reviews", [])
                                    for row in program.get("plan_revisions", [])):
                        result.update(state="needs_review", can_confirm=False, challenge=None)
                except (ctl().ControlError, ValueError, TypeError, KeyError, OSError) as exc:
                    result.update(state="blocked", blocked=True, reasons=["report_or_evidence_invalid:" + str(exc)], available_actions=[], challenge=None)
                    result["can_confirm"] = False
        result["revision"] = digest({"coordinator": ctl().metadata_hash(state["coord_meta"]),
                                      "child": ctl().metadata_hash(state["meta"]), "generation": program["generation"],
                                      "coord_tail": state["coord_events"][-1]["event_hash"],
                                      "child_tail": state["events"][-1]["event_hash"],
                                      "state": result["state"], "challenge": result["challenge"], "reasons": result["reasons"]})
        if result["challenge"] is not None:
            result["challenge"].update(program_id=result["program_id"], stage_id=sid, revision=result["revision"])
        if result["acceptance_snapshot"] is not None:
            result["acceptance_snapshot"]["revision"] = result["revision"]
    except (ctl().ControlError, ValueError, TypeError, KeyError, OSError) as exc:
        result.update(enabled=True, blocked=True, state="blocked", reasons=[str(exc)], challenge=None)
    return result


def require_action(directory, action, *, expected_generation=None, git_provider=None, require_git_provider=False,
                   _metadata_override=None, _allow_pending=False):
    retired = projection(directory, git_provider=git_provider, require_git_provider=require_git_provider,
                         _metadata_override=_metadata_override, _allow_pending=_allow_pending)
    if retired["state"] == "superseded" and action not in {"status", "trace", "check", "inspect"}:
        fail("superseded stage is read-only", action=action, stage=retired)
    if action in READ_ONLY:
        state = retired
        if state["enabled"] and expected_generation is not None and state.get("generation") != expected_generation:
            fail("generation changed before read-only workflow publication", stage=state)
        return state
    state = retired
    if not state["enabled"]:
        return state
    if state["blocked"] or not state.get("stage_id") or (state["state"] == "accepted" and action != "record_visual_review"):
        fail("action is blocked", action=action, stage=state)
    if expected_generation is not None and expected_generation != state["generation"]:
        fail("generation changed before execution/publication", stage=state)
    neutral = {"candidate", "execution_root", "metadata", "transition", "review", "record_visual_review", "agent"}
    if action not in neutral and action not in state["available_actions"]:
        fail("action exceeds this stage's planned authorization", action=action, stage=state)
    candidate_meta = _metadata_override if _metadata_override is not None else ctl().load_metadata(directory)
    if action in CODE_ACTIONS and (candidate_meta.get("candidate_tracking") or {}).get("mode") != "enabled":
        fail("candidate tracking must be enabled before code execution", action=action)
    return state


def _report_bundle_reviews(bundle, run, metadata, reader):
    """Select presentation rows through the accepted bundle's exact references.

    This does not filter frozen facts or negative-review adjudication. Bind to
    the selected run's candidate (which can be a valid same-content ancestor).
    """
    cell = {key: run.get(key) for key in ("layer", "consumer", "scenario")}
    if any(bundle[key] != run[key] for key in ("run_id", "candidate_id")):
        raise ValueError("report bundle/run identity mismatch")
    binding = {"run_id": run["run_id"], "candidate_id": run["candidate_id"],
               "contract_digest": bundle["contract_sha256"], "cell": cell, "outcome": "pass"}
    resolve = review_validator(metadata)

    def bound(ref):
        receipt = resolve(ref)
        if any(receipt.get(key) != value for key, value in binding.items()):
            raise ValueError("report review binding mismatch")
        return receipt

    items = {item["id"]: item for item in bundle["items"]}
    if len(items) != len(bundle["items"]):
        raise ValueError("duplicate report item")
    visual, evidence = [], []
    for group in (bundle.get("visual") or {}).get("frame_sets", []):
        if any(group.get(key) != value for key, value in cell.items()):
            raise ValueError("report frame group cell mismatch")
        for frame in group["frames"]:
            source, preview = items[frame["source_item_id"]], items[frame["preview_item_id"]]
            frame_key = (run["run_id"], run["candidate_id"], *cell.values(), group["id"], frame["id"])
            for ref in frame["review_refs"]:
                receipt = bound(ref)
                for channel in receipt["channels"]:
                    rows = [row for row in receipt["frame_results"] if row.get("frame_set_id") == group["id"]
                            and row.get("frame_id") == frame["id"] and row.get("channel") == channel]
                    if len(rows) != 1 or rows[0].get("input_sha256") != preview["sha256"] \
                            or rows[0].get("source_sha256") != source["sha256"] or rows[0].get("outcome") != "pass":
                        raise ValueError("report frame/channel/hash binding mismatch")
                    visual.append((receipt, rows[0], reader.path(preview["path"]), frame_key))
    evidence_contract = metadata.get("verification_contract", {}).get("evidence_contract") or {}
    requirements = {row["id"]: row for row in evidence_contract.get("required_items", [])}
    for item in items.values():
        req = requirements[item["requirement_id"]]
        # Mirror evidence_bundle's selection: optional machine metadata is not
        # an authenticated review; visual AI references belong to frame_refs.
        visual_review = evidence_contract.get("visual") is not None and item["kind"] in {"frame_set", "visual_review"} \
            and req["method"] != "human_observation"
        if visual_review or req["method"] == "machine" and item["kind"] != "visual_review":
            continue
        ref = item.get("metadata", {}).get("review_ref")
        receipt = bound(ref)
        expected = {"requirement_id": item["requirement_id"], "item_sha256": item["sha256"],
                    "method": req["method"]}
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError("report item review binding mismatch")
        evidence.append(receipt)
    return visual, evidence


def _report_text(snapshot, state):
    """Human-facing report; the separate JSON retains the exact frozen facts."""
    def safe(value):
        return str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
    contract = snapshot["evidence_metadata"].get("verification_contract") or {}
    runs = snapshot["accepted_runs"]
    stages = state["program"]["plan"]["stages"]
    position = next(i for i, row in enumerate(stages) if row["id"] == state["stage_id"])
    next_stage = stages[position + 1] if position + 1 < len(stages) else None
    lines = ["# 阶段验收报告", "", "结论：**" + snapshot["outcome"] + "**", "",
             "阶段：" + safe(stages[position]["title"]) + "（" + safe(state["stage_id"]) + "）", "",
             "已验层级：" + (", ".join(sorted({safe(run.get("layer")) for run in runs})) or "尚无合格验证") + "。", "",
             "facts_digest: " + snapshot["facts_digest"], "",
             "| 验收单元 | 运行 | 结果 | 实测指标 |", "| --- | --- | --- | --- |"]
    for cell in ctl().verification_evidence_module().required_cells(contract) or []:
        run = next((row for row in runs if tuple(row.get(k) for k in ("layer", "consumer", "scenario")) == tuple(cell)), None)
        lines.append("| " + " / ".join(safe(v) for v in cell) + " | " + safe(run.get("run_id") if run else "缺失") +
                     " | " + safe(run.get("outcome") if run else "未满足") + " | " +
                     safe(json.dumps(run.get("metrics") or {}, ensure_ascii=False) if run else "无实测值") + " |")
    lines.extend(["", "必需原始证据：", "", "| 要求 | 方法 | 完整性与数量 |", "| --- | --- | --- |"])
    for row in (contract.get("evidence_contract") or {}).get("required_items") or []:
        lines.append("| " + safe(row["id"]) + " | " + safe(row["method"]) + " | " +
                     safe(row["min_count"]) + " 项；" + ("必须完整" if row["complete"] else "按合同检查") + " |")
    lines.extend(["", "证据引用：", ""])
    visual, evidence = [], []
    for run in runs:
        ref = run.get("evidence_bundle")
        if isinstance(ref, dict) and isinstance(ref.get("path"), str):
            path = Path(state["directory"]) / ref["path"]
            lines.append("- 运行 " + safe(run["run_id"]) + "：[证据包](<" + path.as_posix() + ">)，摘要 " + safe(ref.get("sha256")))
            try:
                reader = ctl().verification_evidence_module().bundle_module().Reader(state["directory"])
                bundle = reader.json(reader.read(ref["path"], ref["sha256"]))
                # Eligible runs also include legitimate fail/inconclusive
                # partial bundles. Keep their result and raw links, without
                # imposing the completed pass-review contract on those items.
                selected_visual, selected_evidence = (_report_bundle_reviews(bundle, run, snapshot["evidence_metadata"], reader)
                                                       if run.get("outcome") == "pass" else ([], []))
                reader.finish()
                visual.extend(selected_visual)
                evidence.extend(selected_evidence)
                for item in bundle["items"]:
                    target = Path(state["directory"]) / item["path"]
                    lines.append("  - " + safe(item["requirement_id"]) + " / " + safe(item["id"]) +
                                 "：[原始/派生文件](<" + target.as_posix() + ">)，" + safe(item["bytes"]) + " 字节。")
            except (ValueError, OSError, KeyError, TypeError) as exc:
                fail("selected evidence cannot be rendered safely", run_id=run.get("run_id"), error=str(exc))
    lines.extend(["", "逐帧视觉观察：", ""])
    if not visual:
        lines.append("本次没有已执行视觉回执；涉及视觉的合同须补齐实际通道审查。" if (contract.get("evidence_contract") or {}).get("visual") else "本阶段合同没有视觉验收项。")
    previews, displayed, receipts = set(), set(), {}
    for receipt, row, preview, frame_key in visual:
        receipts[receipt["review_id"]] = receipt
        row_key = (*frame_key, receipt["review_id"], row["channel"])
        if row_key in displayed:
            continue
        displayed.add(row_key)
        lines.append("- " + safe(row.get("frame_set_id")) + " / " + safe(row.get("frame_id")) + "：" + safe(row.get("outcome")) +
                     "；" + safe(row.get("conclusion")) + "（" + safe(row.get("channel")) + "/" + safe(row.get("model")) + "）。")
        if frame_key not in previews and len(previews) < 5:
            lines.extend(["", "![代表性帧 " + safe(row.get("frame_id")) + "](<" + preview.as_posix() + ">)", ""])
            previews.add(frame_key)
    for receipt in {row["review_id"]: row for row in evidence}.values():
        lines.append("- " + safe(receipt.get("requirement_id")) + "（" + safe(receipt.get("method")) + "）：" + safe(receipt.get("conclusion")))
    limitations = [*snapshot["reasons"], *(value for row in receipts.values() for value in row.get("limitations") or [])]
    lines.extend(["", "限制与最小补测：", ""])
    lines.extend("- " + safe(value) for value in dict.fromkeys(limitations))
    if not limitations:
        lines.append("- 结论仅覆盖上述合同、候选、验证环境及采样范围。")
    lines.extend(["", "下一阶段范围：" + (safe(next_stage["title"]) + "；允许动作 " + ", ".join(next_stage["required_actions"]) if next_stage else "计划内全部阶段结束。"), "",
                  "人工确认仅接受本阶段报告，不扩大设备、部署、刷写或 Git 权限。", ""])
    return "\n".join(lines)


def review_stage(directory, *, request_id, git_provider=None, require_git_provider=False):
    api = ctl()
    if not isinstance(request_id, str) or not request_id.strip():
        fail("review requires nonempty request_id")
    access = _access(git_provider, require_git_provider)
    require_action(directory, "review", **access)
    state = _resolve(directory, **access)
    if state is None or state["stage_id"] is None:
        fail("review requires a bound stage ticket")
    if consolidation_module().replacements(state["program"], state["stage_id"]) is not None:
        fail("superseded stage cannot publish a new review")
    # All expensive evidence/candidate work happens before taking writer locks.
    snapshot = _facts(state, **access)
    prior = next((row for row in state["program"]["reviews"].get(state["stage_id"], []) if row.get("request_id") == request_id), None)
    if prior is not None:
        if prior["facts_digest"] != snapshot["facts_digest"]:
            fail("review request_id conflicts with changed facts")
        return {"ok": True, "already_applied": True, "outcome": prior["outcome"], "review_id": prior["review_id"],
                "report_path": str(Path(directory) / prior["report_path"]), "projection": projection(directory, **access)}
    review_id = str(uuid.uuid4())
    snapshot.update(schema_version=1, review_id=review_id, stage_id=state["stage_id"],
                    program_id=state["program"]["plan"]["program_id"], at=api.now_iso())
    report_dir = Path(directory).resolve() / ".stage_acceptance" / review_id
    raw_json = (json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    text = _report_text(snapshot, state)
    review = {"review_id": review_id, "request_id": request_id, "outcome": snapshot["outcome"], "facts_digest": snapshot["facts_digest"],
              "review_path": (report_dir / "stage_review.json").relative_to(directory).as_posix(),
              "review_sha256": hashlib.sha256(raw_json).hexdigest(),
              "report_path": (report_dir / "stage_acceptance.md").relative_to(directory).as_posix(),
              "report_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
              "challenge_id": str(uuid.uuid4()) if snapshot["outcome"] == "pass" else None, "snapshot": snapshot}
    with api.DirLock(state["coordinator"]), api.DirLock(Path(directory)):
        current = _resolve(directory, **access)
        if consolidation_module().replacements(current["program"], current["stage_id"]) is not None:
            fail("stage retired while preparing review; no report written")
        fresh = _facts(current, **access)
        if current["program"]["generation"] != state["program"]["generation"] or fresh["facts_digest"] != snapshot["facts_digest"]:
            fail("facts/generation changed while preparing review; no report written")
        # Qualification and first business write share the revision's locks.
        # No provider/model work is performed while these locks are held.
        report_dir.mkdir(parents=True, exist_ok=False)
        for name, raw in (("stage_review.json", raw_json), ("stage_acceptance.md", text.encode("utf-8"))):
            with (report_dir / name).open("xb") as handle:
                handle.write(raw)
        program = copy.deepcopy(current["program"])
        program["reviews"].setdefault(state["stage_id"], []).append(review)
        program["generation"] += 1
        def publication_check():
            if _facts(current, **access)["facts_digest"] != snapshot["facts_digest"]:
                fail("facts changed immediately before review publication")
        _write(state["coordinator"], current["coord_meta"], "stage_program", program, "review", request_id,
               before_append=publication_check, **access)
    return {"ok": True, "outcome": snapshot["outcome"], "review_id": review_id,
            "report_path": str(report_dir / "stage_acceptance.md"), "facts_digest": snapshot["facts_digest"],
            "reasons": snapshot["reasons"], "projection": projection(directory, **access)}


def confirm_stage(directory, request, *, trusted_confirmation=None, git_provider=None, require_git_provider=False):
    api = ctl()
    if not callable(trusted_confirmation):
        fail("trusted host/UI confirmation callback required; CLI actor/JSON is not human provenance")
    if not isinstance(request, dict):
        fail("confirmation request must be an object")
    decision = request.get("decision", "confirm")
    intents = {"confirm": "确认本阶段验收", "reject": "拒绝本阶段验收", "revoke": "撤销本阶段验收"}
    if decision not in intents:
        fail("unknown confirmation decision")
    source = trusted_confirmation(copy.deepcopy(request))
    if not isinstance(source, dict) or source.get("source") not in {"ui", "host_user"} \
            or source.get("intent") != intents[decision] or not isinstance(source.get("source_id"), str) or not source["source_id"].strip():
        fail("trusted callback did not attest explicit user decision")
    required = {"program_id", "stage_id", "challenge_id", "facts_digest", "report_sha256", "revision", "request_id"}
    if not required.issubset(request) or any(not isinstance(request[k], str) or not request[k].strip() for k in required):
        fail("confirmation requires exact program/stage/challenge/facts/report/revision/request_id")
    if not set(request).issubset(required | {"decision", "reason", "expected_ticket_revision"}):
        fail("unexpected confirmation fields")
    access = _access(git_provider, require_git_provider)
    state = _resolve(directory, **access)
    if state is None or state["stage_id"] is None:
        fail("confirmation requires a stage ticket")
    with api.DirLock(state["coordinator"]), api.DirLock(Path(directory)):
        state = _resolve(directory, **access)
        program = copy.deepcopy(state["program"])
        prior = next((row for row in program["decisions"] if row["request_id"] == request["request_id"]), None)
        request_digest = digest(request)
        if prior is not None:
            if prior["request_digest"] != request_digest or prior["source"] != source:
                fail("request_id replay payload/source conflict")
            return {"ok": True, "already_applied": True, "decision_id": prior["decision_id"]}
        view = projection(directory, **access)
        review = _latest_review(program, state["stage_id"])
        if review is None or review["outcome"] != "pass":
            fail("fail/inconclusive reviews cannot be manually promoted to pass")
        if (view["blocked"] and decision != "revoke") or view["revision"] != request["revision"]:
            fail("confirmation is blocked or revision is stale", stage=view)
        if decision in {"confirm", "reject"} and view["state"] != "awaiting_confirmation":
            fail("no current confirmation challenge; review again", stage=view)
        if decision == "revoke" and not view.get("can_revoke"):
            fail("only an accepted stage can be revoked")
        if decision == "revoke" and (not isinstance(request.get("reason"), str) or not request["reason"].strip()):
            fail("revocation requires an explicit reason")
        expected = {"program_id": program["plan"]["program_id"], "stage_id": state["stage_id"],
                    "challenge_id": review["challenge_id"], "facts_digest": review["facts_digest"], "report_sha256": review["report_sha256"]}
        if any(request[k] != v for k, v in expected.items()):
            fail("confirmation snapshot mismatch")
        row = {"decision_id": str(uuid.uuid4()), "review_id": review["review_id"], "stage_id": state["stage_id"],
               "decision": decision, "at": api.now_iso(), "source": source, "request_id": request["request_id"],
               "request_digest": request_digest, "snapshot": expected, "reason": request.get("reason", "")}
        program["decisions"].append(row)
        program["generation"] += 1
        def publication_check():
            if decision == "confirm":
                current_facts = _facts(state, **access)
                if current_facts["outcome"] != "pass" or current_facts["facts_digest"] != review["facts_digest"]:
                    fail("facts changed immediately before confirmation publication")
                _check_report(state["directory"], review)
        _write(state["coordinator"], state["coord_meta"], "stage_program", program, "decision", request["request_id"],
               before_append=publication_check, **access)
    return {"ok": True, "already_applied": False, "decision_id": row["decision_id"], "decision": decision,
            "projection": projection(directory, **access)}
