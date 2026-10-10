"""Bounded, read-only final-source snapshots and receipt applicability.

No Git writes, external diff, hooks, filters or model calls. Snapshot hashes
establish source identity; only real successful step receipts establish review.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import time

DEFAULT_LIMITS = {"max_files": 50000, "max_bytes": 512 * 1024 * 1024,
                  "max_git_bytes": 16 * 1024 * 1024, "timeout_seconds": 30}
REVIEW_STEPS = ("code", "deepcheck", "audit")
CONTROL_DIRS = (".icode_output",)


class CandidateError(ValueError):
    """Identity cannot be established completely inside the resource boundary."""


_INSPECTION = None


def _git(root, args, limit):
    global _INSPECTION
    if _INSPECTION is None:
        spec = importlib.util.spec_from_file_location(
            "candidate_inspection_git", Path(__file__).with_name("inspection_worklist.py"))
        _INSPECTION = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_INSPECTION)
    try:
        data, truncated = _INSPECTION._git(root, args, limit)
    except _INSPECTION.InspectionError as exc:
        raise CandidateError(str(exc)) from exc
    if truncated:
        raise CandidateError("candidate Git scan exceeds max_git_bytes (no truncation allowed)")
    return data


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def snapshot_identity_ok(snapshot):
    """Offline closed-shape/hash check for linter and evidence consumers."""
    if not isinstance(snapshot, dict):
        return False
    try:
        unhashed = {k: v for k, v in snapshot.items() if k != "candidate_id"}
        content_keys = ("identity", "base", "code_files", "scope_contract", "inspection_scope",
                        "excluded_side_effects", "control_artifact_exclusions", "manifest")
        if set(snapshot) != set(content_keys) | {"version", "head", "head_tree", "index_manifest",
                                                  "untracked_manifest", "content_id", "candidate_id"}:
            return False
        identity = snapshot["identity"]
        if not isinstance(identity, dict) or set(identity) != {"common_git_dir", "checkout_realpath",
                "object_format", "common_git_device", "common_git_inode", "checkout_device", "checkout_inode"}:
            return False
        if identity["object_format"] not in ("sha1", "sha256"):
            return False
        oid_pattern = r"[0-9a-f]{40}" if identity["object_format"] == "sha1" else r"[0-9a-f]{64}"
        if any(not isinstance(snapshot[key], str) or re.fullmatch(oid_pattern, snapshot[key]) is None
               for key in ("base", "head", "head_tree")):
            return False
        if any(not isinstance(identity[k], str) or not Path(identity[k]).is_absolute()
               for k in ("common_git_dir", "checkout_realpath")):
            return False
        if any(type(identity[k]) is not int or identity[k] < 0 for k in
               ("common_git_device", "common_git_inode", "checkout_device", "checkout_inode")):
            return False
        for key in ("code_files", "excluded_side_effects", "control_artifact_exclusions"):
            if not isinstance(snapshot[key], list) or snapshot[key] != _paths(snapshot[key]):
                return False
        if snapshot["control_artifact_exclusions"] != list(CONTROL_DIRS) or \
                "." in snapshot["code_files"] or "." in snapshot["excluded_side_effects"]:
            return False
        if not isinstance(snapshot["inspection_scope"], dict) or \
                (snapshot["scope_contract"] is not None and not isinstance(snapshot["scope_contract"], dict)):
            return False
        modes = {"file": {"100644", "100755"}, "symlink": {"120000"}, "gitlink": {"160000"}}
        for key in ("manifest", "untracked_manifest", "index_manifest"):
            rows = snapshot[key]
            if not isinstance(rows, list):
                return False
            paths = []
            for row in rows:
                required = {"path", "mode", "oid"} if key == "index_manifest" else \
                           {"path", "kind", "mode", "sha256", "size", "git_oid"}
                if not isinstance(row, dict) or set(row) != required:
                    return False
                paths.append(row["path"])
                if key != "index_manifest" and (row["kind"] not in modes or row["mode"] not in modes[row["kind"]]
                        or type(row["size"]) is not int or row["size"] < 0
                        or not isinstance(row["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None):
                    return False
                oid = row["oid"] if key == "index_manifest" else row["git_oid"]
                if row["mode"] not in {"100644", "100755", "120000", "160000"} or \
                        not isinstance(oid, str) or re.fullmatch(oid_pattern, oid) is None:
                    return False
            if paths != _paths(paths) or "." in paths:
                return False
        manifest_by_path = {row["path"]: row for row in snapshot["manifest"]}
        if any(manifest_by_path.get(row["path"]) != row for row in snapshot["untracked_manifest"]):
            return False
        return snapshot.get("version") == 1 and type(snapshot.get("version")) is int \
            and snapshot.get("candidate_id") == _digest(unhashed) \
            and snapshot.get("content_id") == _digest({k: snapshot[k] for k in content_keys})
    except (ValueError, TypeError, KeyError):
        return False


def _tree_entries(raw, exclusions=()):
    result = {}
    for row in raw.split(b"\0"):
        if row:
            info, path = row.split(b"\t", 1)
            mode, kind, oid = info.decode("ascii").split()
            name = os.fsdecode(path)
            if not _excluded(name, exclusions):
                result[name] = {"mode": mode, "oid": oid}
    return result


def _paths(values):
    if not isinstance(values, (tuple, list)):
        raise CandidateError("candidate paths must be an array")
    result = set()
    for raw in values:
        if not isinstance(raw, str) or not raw or "\0" in raw or "\\" in raw:
            raise CandidateError("candidate path must be a relative POSIX string")
        value = Path(raw)
        if value.is_absolute() or ".." in value.parts or ".git" in value.parts:
            raise CandidateError(f"unsafe candidate path: {raw}")
        normalized = value.as_posix()
        result.add(normalized)
    return sorted(result)


def _excluded(path, exclusions):
    return any(path == x or path.startswith(x + "/") or
               (x in CONTROL_DIRS and x in Path(path).parts) for x in exclusions)


def repository_head(workspace):
    """Return an existing checkout's HEAD, or None for a non-Git/unborn fixture."""
    root = Path(workspace).resolve()
    if not (root / ".git").exists():
        return None
    top = _git(root, ["rev-parse", "--show-toplevel"], 8192).decode().strip()
    if Path(top).resolve() != root:
        raise CandidateError("Git marker does not identify this checkout root")
    try:
        return _git(root, ["rev-parse", "--verify", "HEAD^{commit}"], 256).decode().strip()
    except CandidateError:
        # A valid unborn branch is distinct from inaccessible/corrupt Git.
        reference = _git(root, ["symbolic-ref", "--quiet", "HEAD"], 8192).decode().strip()
        refs = _git(root, ["for-each-ref", "--format=%(refname)", "refs/heads/"], 8192).decode().splitlines()
        if reference.startswith("refs/heads/") and reference not in refs:
            return None
        raise


def normalize_limits(limits=None):
    bounds = dict(DEFAULT_LIMITS)
    if limits is not None:
        if not isinstance(limits, dict) or set(limits) - set(bounds):
            raise CandidateError("invalid candidate limits")
        bounds.update(limits)
    if any(type(x) is not int or x < 1 for x in bounds.values()):
        raise CandidateError("candidate limits must be positive integers")
    return bounds


def capture_candidate(workspace, *, base, code_files=None, scope_contract=None,
                      excluded_side_effects=None, inspection_scope=None, limits=None):
    """Capture all tracked final bytes and visible untracked files, fail closed.

    Git-ignored untracked files and ICODE control artifacts are outside this
    source candidate; explicitly listed code_files remain included even when
    Git ignores them. User exclusions are exact relative paths/directories,
    never inferred from a changed file. Symlink contents are link target bytes;
    symlink parent traversal and special files are rejected.
    """
    bounds = normalize_limits(limits)
    deadline = time.monotonic() + bounds["timeout_seconds"]
    root = Path(workspace).resolve()
    git_limit = bounds["max_git_bytes"]

    def git(args, limit=git_limit):
        if time.monotonic() > deadline:
            raise CandidateError("candidate capture timeout")
        return _git(root, args, limit)

    top = git(["rev-parse", "--show-toplevel"], 8192).decode().strip()
    if Path(top).resolve() != root:
        raise CandidateError("candidate workspace must be a Git checkout root")
    common_raw = git(["rev-parse", "--git-common-dir"], 8192).decode().strip()
    common = (root / common_raw).resolve()
    common_stat, checkout_stat = common.stat(), root.stat()
    head = git(["rev-parse", "--verify", "HEAD^{commit}"], 256).decode().strip()
    if not isinstance(base, str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base) is None:
        raise CandidateError("candidate base must be a resolved full commit oid")
    resolved_base = git(["rev-parse", "--verify", base + "^{commit}"], 256).decode().strip()
    if resolved_base != base:
        raise CandidateError("candidate base is not a commit")
    object_format = git(["rev-parse", "--show-object-format"], 256).decode().strip()
    if object_format not in ("sha1", "sha256"):
        raise CandidateError("unsupported Git object format")
    exclusions = _paths(excluded_side_effects or [])
    if "." in exclusions:
        raise CandidateError("excluding the entire candidate is prohibited")
    files = _paths(code_files or [])
    if "." in files:
        raise CandidateError("code_files must name files")
    if any(_excluded(x, [*CONTROL_DIRS, *exclusions]) for x in files):
        raise CandidateError("code_files overlap excluded/control artifacts")
    # Trees/index are plumbing manifests. No diff drivers, textconv or filters.
    head_raw = git(["ls-tree", "-rz", "--full-tree", "HEAD"])
    index_raw = git(["ls-files", "--stage", "-z"])
    others_raw = git(["ls-files", "--others", "--exclude-standard", "-z"])
    head_entries, index_entries = {}, {}
    for row in head_raw.split(b"\0"):
        if row:
            info, path = row.split(b"\t", 1)
            mode, kind, oid = info.decode("ascii").split()
            head_entries[os.fsdecode(path)] = {"mode": mode, "oid": oid}
    for row in index_raw.split(b"\0"):
        if row:
            info, path = row.split(b"\t", 1)
            mode, oid, stage = info.decode("ascii").split()
            if stage != "0":
                raise CandidateError("unmerged candidate index is blocked")
            index_entries[os.fsdecode(path)] = {"mode": mode, "oid": oid}
    untracked = sorted({os.fsdecode(x) for x in others_raw.split(b"\0") if x})
    names = sorted(set(head_entries) | set(index_entries) | set(untracked) | set(files))
    if len(names) > bounds["max_files"]:
        raise CandidateError("candidate scan exceeds max_files")
    manifest, total_bytes = [], 0
    for name in names:
        _paths([name])
        if _excluded(name, [*CONTROL_DIRS, *exclusions]):
            continue
        path = root / name
        if any(p.is_symlink() for p in path.parents if p == root or root in p.parents):
            raise CandidateError(f"candidate symlink parent: {name}")
        if time.monotonic() > deadline:
            raise CandidateError("candidate capture timeout")
        try:
            before = path.lstat()
        except FileNotFoundError:
            continue  # Deleted paths are bound by absence in the final manifest.
        index = index_entries.get(name) or head_entries.get(name) or {}
        if index.get("mode") == "160000":
            if not stat.S_ISDIR(before.st_mode):
                raise CandidateError(f"submodule unavailable: {name}")
            remaining = dict(bounds, max_bytes=max(1, bounds["max_bytes"] - total_bytes),
                             timeout_seconds=max(1, int(deadline - time.monotonic())))
            try:
                sub = capture_candidate(path, base=index["oid"], limits=remaining)
                tree = _tree_entries(_git(path, ["ls-tree", "-rz", "HEAD"], git_limit), CONTROL_DIRS)
                expected = {r["path"]: {"mode": r["mode"], "oid": r["git_oid"]} for r in sub["manifest"]}
                staged = {r["path"]: {"mode": r["mode"], "oid": r["oid"]} for r in sub["index_manifest"]}
                if sub["head"] != index["oid"] or expected != tree or staged != tree or sub["untracked_manifest"]:
                    raise CandidateError(f"dirty or changed submodule: {name}")
            except (CandidateError, OSError) as exc:
                raise CandidateError(f"submodule is not provably clean: {name}: {exc}") from exc
            total_bytes += sum(r["size"] for r in sub["manifest"])
            if total_bytes > bounds["max_bytes"]:
                raise CandidateError("candidate submodule content exceeds max_bytes")
            manifest.append({"path": name, "kind": "gitlink", "mode": "160000",
                             "sha256": sub["content_id"], "size": sum(r["size"] for r in sub["manifest"]),
                             "git_oid": sub["head"]})
            continue
        if stat.S_ISLNK(before.st_mode):
            data = os.fsencode(os.readlink(path))
            digest = hashlib.sha256(data)
            blob = hashlib.new(object_format, b"blob " + str(len(data)).encode() + b"\0" + data)
            size, mode, kind = len(data), "120000", "symlink"
            total_bytes += size
        elif stat.S_ISREG(before.st_mode):
            digest = hashlib.sha256()
            blob = hashlib.new(object_format, b"blob " + str(before.st_size).encode() + b"\0")
            size, mode, kind = 0, "100755" if before.st_mode & 0o111 else "100644", "file"
            # O_NOFOLLOW prevents a last-moment replacement by a symlink/device.
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise CandidateError(f"candidate file replaced while reading: {name}")
                while True:
                    block = stream.read(65536)
                    if not block:
                        break
                    size += len(block)
                    total_bytes += len(block)
                    if total_bytes > bounds["max_bytes"] or time.monotonic() > deadline:
                        raise CandidateError("candidate content exceeds max_bytes/time budget")
                    digest.update(block)
                    blob.update(block)
            after = path.lstat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != \
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise CandidateError(f"candidate changed during capture: {name}")
        else:
            raise CandidateError(f"candidate special file/directory is blocked: {name}")
        if total_bytes > bounds["max_bytes"]:
            raise CandidateError("candidate content exceeds max_bytes")
        row = {"path": name, "kind": kind, "mode": mode, "sha256": digest.hexdigest(),
               "size": size, "git_oid": blob.hexdigest()}
        # Hydrated LFS bytes are hashed directly. A matching staged LFS pointer
        # permits proving commit tree equivalence without invoking its filter.
        oid = index.get("oid")
        if oid and oid != row["git_oid"]:
            try:
                pointer = git(["cat-file", "blob", oid], 4096)
            except CandidateError:
                pointer = b""
            match = re.fullmatch(rb"version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n", pointer)
            if match and match[1].decode() == row["sha256"] and int(match[2]) == size:
                row["git_oid"] = oid
        manifest.append(row)
    # Check Git identities again, so concurrent index/HEAD changes never yield a
    # mixed snapshot. Every file is race checked above; this is not a FS lock.
    if head != git(["rev-parse", "HEAD"], 256).decode().strip() or \
            index_raw != git(["ls-files", "--stage", "-z"]) or \
            others_raw != git(["ls-files", "--others", "--exclude-standard", "-z"]):
        raise CandidateError("candidate Git state changed during capture")
    identity = {"common_git_dir": str(common), "checkout_realpath": str(root),
                "object_format": object_format,
                "common_git_device": common_stat.st_dev, "common_git_inode": common_stat.st_ino,
                "checkout_device": checkout_stat.st_dev, "checkout_inode": checkout_stat.st_ino}
    content = {"identity": identity, "base": base, "code_files": files,
               "scope_contract": scope_contract, "inspection_scope": inspection_scope or {},
               "excluded_side_effects": exclusions, "control_artifact_exclusions": list(CONTROL_DIRS),
               "manifest": manifest}
    snapshot = {"version": 1, **content, "head": head,
                "head_tree": git(["rev-parse", "HEAD^{tree}"], 256).decode().strip(),
                "index_manifest": [{"path": k, **v} for k, v in sorted(index_entries.items())
                                   if not _excluded(k, [*CONTROL_DIRS, *exclusions])],
                "untracked_manifest": [row for row in manifest if row["path"] in untracked],
                "content_id": _digest(content)}
    snapshot["candidate_id"] = _digest(snapshot)
    return snapshot


def compare_candidates(recorded, current):
    """Exact equality or strict linear same-final-content commit proof."""
    if not snapshot_identity_ok(recorded) or not snapshot_identity_ok(current):
        return {"equivalent": False, "reason": "candidate_invalid"}
    if recorded.get("candidate_id") == current.get("candidate_id"):
        return {"equivalent": True, "reason": "identical_candidate"}
    if recorded.get("content_id") != current.get("content_id"):
        return {"equivalent": False, "reason": "source_or_contract_drift"}
    if recorded.get("head") == current.get("head"):
        return {"equivalent": False, "reason": "index_or_manifest_drift"}
    root = Path(current["identity"]["checkout_realpath"])
    old_head, new_head = recorded["head"], current["head"]
    try:
        ancestors = _git(root, ["rev-list", "--parents", "--ancestry-path", f"{old_head}..{new_head}"],
                         DEFAULT_LIMITS["max_git_bytes"]).decode().splitlines()
        # Require the whole new history to extend the reviewed HEAD linearly.
        # Cherry-pick/rebase/amend/merge with identical bytes still need review.
        expected_parent = old_head
        if not ancestors:
            return {"equivalent": False, "reason": "head_history_changed"}
        if len(ancestors) > 64:
            return {"equivalent": False, "reason": "commit_proof_budget_exceeded"}
        for line in reversed(ancestors):
            parts = line.split()
            if len(parts) != 2 or parts[1] != expected_parent:
                return {"equivalent": False, "reason": "head_history_changed"}
            expected_parent = parts[0]
        if expected_parent != new_head:
            return {"equivalent": False, "reason": "head_history_changed"}
        exclusions = current["excluded_side_effects"] + current["control_artifact_exclusions"]
        reviewed = {row["path"]: {"mode": row["mode"], "oid": row["git_oid"]}
                    for row in recorded["manifest"]}
        deadline = time.monotonic() + DEFAULT_LIMITS["timeout_seconds"]
        for line in ancestors:
            if time.monotonic() > deadline:
                raise CandidateError("commit equivalence timeout")
            tree = _git(root, ["ls-tree", "-rz", "--full-tree", line.split()[0]], DEFAULT_LIMITS["max_git_bytes"])
            if _tree_entries(tree, exclusions) != reviewed:
                return {"equivalent": False, "reason": "commit_tree_not_reviewed_content"}
    except (CandidateError, KeyError, UnicodeError):
        return {"equivalent": False, "reason": "commit_equivalence_unproven"}
    return {"equivalent": True, "reason": "same_content_commit", "from_head": old_head, "to_head": new_head}


def review_source_equivalence(recorded, current):
    """A review may establish its declaration, but may not silently change source."""
    if not snapshot_identity_ok(recorded) or not snapshot_identity_ok(current):
        return {"equivalent": False, "reason": "review_candidate_missing"}
    normalized = dict(current, inspection_scope=recorded["inspection_scope"])
    keys = ("identity", "base", "code_files", "scope_contract", "inspection_scope",
            "excluded_side_effects", "control_artifact_exclusions", "manifest")
    normalized["content_id"] = _digest({k: normalized[k] for k in keys})
    normalized["candidate_id"] = _digest({k: v for k, v in normalized.items() if k != "candidate_id"})
    return compare_candidates(recorded, normalized)


def delivery_freshness(meta, current, events=None, required_steps=REVIEW_STEPS):
    """Read-only applicability; history/status alone never establish a pass."""
    tracking = meta.get("candidate_tracking") or {}
    if tracking.get("mode") != "enabled":
        return {"ok": False, "state": "legacy_untracked", "reasons": ["candidate_tracking_not_enabled"],
                "candidate_id": None, "applicable_receipts": [], "required_steps": list(required_steps)}
    applicable, reasons, stale = [], [], False
    if current is None:
        return {"ok": False, "state": "blocked", "reasons": ["candidate_capture_unavailable"],
                "candidate_id": None, "applicable_receipts": [], "required_steps": list(required_steps)}
    finished = {e.get("event_id"): e for e in events or [] if e.get("event_type") == "step_finished"}
    latest = {}
    for event in events or []:
        payload = event.get("payload") or {}
        if event.get("event_type") in ("step_started", "step_finished") and payload.get("step") in REVIEW_STEPS:
            latest[payload["step"]] = event
    for receipt in meta.get("review_receipts") or []:
        event = finished.get(receipt.get("event_id"))
        payload = (event or {}).get("payload") or {}
        if event is None or payload.get("outcome") != "success" or \
                payload.get("step") != receipt.get("step") or \
                payload.get("attempt") != receipt.get("attempt") or \
                payload.get("candidate_receipt") != receipt:
            reasons.append("unbacked_review_receipt")
            continue
        # New failure/degradation/open retry supersedes the old success even
        # when the source bytes have not changed.
        if latest.get(receipt.get("step"), {}).get("event_id") != receipt.get("event_id"):
            continue
        proof = compare_candidates(receipt.get("candidate"), current)
        if proof["equivalent"]:
            applicable.append({"step": receipt["step"], "attempt": receipt["attempt"],
                               "event_id": receipt["event_id"], "candidate_id": receipt["candidate_id"],
                               "equivalence": proof})
        else:
            stale = True
    missing = sorted(set(required_steps) - {r["step"] for r in applicable})
    reasons.extend("missing_current_" + step for step in missing)
    ok = not reasons
    state = "fresh" if ok else "stale" if stale else "review_required"
    return {"ok": ok, "state": state, "reasons": reasons, "candidate_id": current["candidate_id"],
            "applicable_receipts": applicable, "required_steps": list(required_steps)}
