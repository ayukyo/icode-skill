"""Deterministic, read-only inspection declarations (stdlib only).

Hashes/reads never prove actual Read, understanding or semantic judgment. Rules
are review prompts, not lint results; project LIMIT rules remain authoritative.
No persistence, build, hooks, external diff drivers or LLM calls are performed.
"""
import hashlib
import copy
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time
from queue import Empty, Full, Queue
from threading import Event, Thread


PHASES = {"code": ["code_review"], "deepcheck": ["reverse", "fixed", "free"],
          "audit": ["audit"], "crosscheck": ["fresh"]}
GENERAL = ["project_LIMIT", "dependencies_and_callers", "logic_and_boundaries",
           "errors_and_resources", "compatibility_and_security"]
LANGUAGE = {".c": "c_cpp_lifetime_and_undefined_behavior",
            ".cpp": "c_cpp_lifetime_and_undefined_behavior",
            ".h": "c_cpp_lifetime_and_undefined_behavior",
            ".hpp": "c_cpp_lifetime_and_undefined_behavior",
            ".sh": "shell_quoting_exit_codes_and_portability",
            ".py": "python_types_exceptions_and_mutable_state"}
CONFIG = {".json", ".yaml", ".yml", ".toml", ".ini", ".conf", ".xml"}
HASH = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN = {".git", ".icode_output", ".ssh", ".gnupg", ".aws"}
ASSOCIATED_SUFFIXES = set(LANGUAGE) | CONFIG | {".cc", ".cxx", ".hxx", ".rs", ".go", ".java", ".js", ".ts", ".tsx", ".jsx"}
SOURCE_SUFFIXES = ASSOCIATED_SUFFIXES - CONFIG
CHECKS_VERSION = 1
CHECK_IDENTITY_KEYS = ("check_id", "kind", "version", "paths", "required_items")
CHECK_ITEMS = {
    "timestamp_contract": ["signedness", "domain", "unit", "epoch", "negative_zero_max", "sentinel",
                           "overflow_underflow", "projection", "frame_window", "interval_fallback"],
    "shared_variant_consumers": ["target", "sibling", "unknown", "supported", "unsupported",
        "probe_failure", "read_failure", "stale", "consumers", "memory:mmap", "memory:dma",
        "first_frame", "steady", "stop_start", "reconnect", "build_config:enabled", "build_config:disabled",
        "fallback:old_semantics", "fallback:no_extra_probe", "fallback:error_isolation"],
}
# Deliberately bounded lexical prompts, not claims of automatic semantic analysis.
# Source suffix filtering prevents prose about an SDK from becoming code findings.
TIMESTAMP_SIGNAL = re.compile(r"timestamp|time_stamp|clock|duration|interval|epoch|(?:^|[^a-z0-9])(?:ns|us|ms)(?:[^a-z0-9]|$)", re.I)
INTEGER_CONVERSION = re.compile(r"(?:static_cast\s*<\s*|\(\s*)(?:(?:u?int(?:8|16|32|64)_t)|(?:unsigned|signed)(?:\s+(?:long|short|int))*|(?:long|short)(?:\s+(?:long|int))*)\s*(?:>|\))")
SHARED_SIGNAL = re.compile(r"shared|variant|sibling|product[_a-z0-9]*|device[_a-z0-9]*(?:pid|model)|capabilit|probe[_a-z0-9]*(?:support|fail)|backend", re.I)


class InspectionError(ValueError):
    """Invalid input or an unsafe/inaccessible inspection boundary."""


def _positive(value):
    return type(value) is int and value > 0


def _workspace(workspace):
    try:
        root = Path(os.path.abspath(os.fspath(workspace)))
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise InspectionError("workspace symlink is blocked")
        if not root.is_dir():
            raise InspectionError("workspace must be a directory")
        return root
    except (TypeError, OSError) as exc:
        raise InspectionError(f"invalid workspace: {exc}") from exc


def _path(root, raw, *, directory=False, artifact=False):
    if not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw:
        raise InspectionError("path must be a nonempty relative POSIX string")
    p = Path(raw)
    if p.is_absolute() or ".." in raw.split("/"):
        raise InspectionError(f"unsafe path: {raw}")
    for part in p.parts:
        lower = part.lower()
        # Review/test artifacts may live in ticket sidecars; source candidates
        # still cannot. Private paths and Git internals remain blocked for both.
        blocked = lower in FORBIDDEN and not (artifact and lower == ".icode_output")
        if blocked or lower == ".env" or lower.startswith(".env.") or lower.startswith("id_rsa") or lower.startswith("id_ed25519") or lower in {"credentials", "credentials.json"} or lower.endswith((".pem", ".key", ".p12", ".pfx")):
            raise InspectionError(f"blocked private/control path: {raw}")
    target = root / p
    if any(x.is_symlink() for x in (target, *target.parents) if x == root or root in x.parents):
        raise InspectionError(f"symlink path is blocked: {raw}")
    if not directory and p == Path("."):
        raise InspectionError("file path cannot be workspace")
    return p.as_posix()


def _paths(root, values, *, directory=False):
    if not isinstance(values, (list, tuple)):
        raise InspectionError("paths must be an array")
    return sorted({_path(root, x, directory=directory) for x in values})


def _git_pipe(proc, limit, deadline):
    """Read a bounded subprocess pipe without ``select`` on anonymous pipes.

    Windows ``SelectSelector`` cannot wait on anonymous pipe handles.  A
    bounded reader thread keeps the same stdout and deadline limits while the
    caller remains responsible for killing the process on timeout/overflow.
    The queue is deliberately small so a noisy Git process cannot turn this
    compatibility path into an unbounded memory sink.
    """
    chunks = Queue(maxsize=2)
    stop = Event()
    reader_error = []

    def read_chunks():
        try:
            while not stop.is_set():
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                while not stop.is_set():
                    try:
                        chunks.put(chunk, timeout=0.05)
                        break
                    except Full:
                        continue
        except OSError as exc:
            reader_error.append(exc)
        finally:
            while not stop.is_set():
                try:
                    chunks.put(None, timeout=0.05)
                    break
                except Full:
                    continue

    reader = Thread(target=read_chunks, name="icode-git-pipe", daemon=True)
    reader.start()
    data, truncated = bytearray(), False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InspectionError("git timeout")
            try:
                chunk = chunks.get(timeout=min(0.05, remaining))
            except Empty:
                if not reader.is_alive():
                    break
                continue
            if chunk is None:
                break
            data.extend(chunk)
            if len(data) > limit:
                truncated = True
                proc.kill()
                break
        if proc.poll() is None:
            proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        if reader_error and not truncated:
            raise InspectionError(f"git pipe unavailable: {reader_error[0]}")
        return bytes(data[:limit]), truncated
    finally:
        stop.set()
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.stdout.close()
        except OSError:
            pass
        reader.join(timeout=1)


def _git(repo, args, limit=8192):
    """Bound both stdout memory and elapsed time, even for ls-files/git show."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    command = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
               "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args]
    try:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env) as proc:
            if os.name == "nt":
                data, truncated = _git_pipe(proc, limit, time.monotonic() + 5)
                if proc.returncode and not truncated:
                    raise InspectionError(f"git failed: {args[0]}")
                return data, truncated
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                data, deadline, truncated = bytearray(), time.monotonic() + 5, False
                try:
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise InspectionError("git timeout")
                        for key, _ in selector.select(remaining):
                            chunk = os.read(key.fd, min(65536, limit + 1 - len(data)))
                            if not chunk:
                                selector.unregister(key.fileobj)
                            else:
                                data.extend(chunk)
                                if len(data) > limit:
                                    truncated = True
                                    proc.kill()
                                    selector.unregister(key.fileobj)
                    proc.wait(timeout=max(0.01, deadline - time.monotonic()))
                    if proc.returncode and not truncated:
                        raise InspectionError(f"git failed: {args[0]}")
                    return bytes(data[:limit]), truncated
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InspectionError(f"git unavailable/timeout: {exc}") from exc


def _repo(root, path):
    current = (root / path).parent
    while current == root or root in current.parents:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def _inside(path, scopes):
    return any(s == "." or path == s or path.startswith(s + "/") for s in scopes)


def _stem(path):
    stem = Path(path).stem
    return stem[5:] if stem.startswith("test_") else stem[:-5] if stem.endswith("_test") else stem


def _rules(path):
    suffix = Path(path).suffix.lower()
    extra = LANGUAGE.get(suffix, "config_schema_defaults_and_consumers" if suffix in CONFIG else "format_and_consumer_contract")
    return GENERAL + [extra]


def check_declarations(report):
    """Return only frozen special-rule identity; assessment progress stays mutable."""
    checks = report.get("checks", [])
    if not isinstance(checks, list) or any(not isinstance(c, dict) for c in checks):
        raise InspectionError("checks must be an array of objects")
    return [{key: c.get(key) for key in CHECK_IDENTITY_KEYS} for c in checks]


def _trigger_content(root, path, data, repo, baseline, max_bytes, debt):
    text = data.decode("utf-8")
    if repo is not None and baseline is not None:
        # Only removed lines supplement current source. A removed clock/cast
        # must retain its prompt, without importing unrelated historical code.
        rel = (root / path).relative_to(repo).as_posix()
        try:
            diff, cut = _git(repo, ["diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                "--unified=0", baseline, "--", f":(literal){rel}"], max_bytes)
            text += "\n" + "\n".join(line[1:] for line in diff.decode("utf-8").splitlines()
                                    if line.startswith("-") and not line.startswith("---"))
            if cut:
                debt[path] = "special-rule trigger diff truncated; remaining source unobserved"
        except (InspectionError, UnicodeError, OSError) as exc:
            debt[path] = f"special-rule trigger baseline unobserved: {exc}"
    return text


def _derive_checks(source_groups):
    checks = []
    for label in sorted(source_groups, key=str):
        entries = source_groups[label]
        paths = sorted(entries)
        kinds = set().union(*entries.values())
        for kind in sorted(kinds):
            identity = dict(kind=kind, version=CHECKS_VERSION, paths=paths, required_items=list(CHECK_ITEMS[kind]))
            digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            checks.append(dict(check_id=digest, **identity, results={}))
    return checks


def _content(root, path, repo, baseline, max_bytes, *, artifact=False):
    target = root / _path(root, path, artifact=artifact)
    if target.exists():
        if not stat.S_ISREG(target.stat().st_mode):
            raise InspectionError("not a regular file")
        with target.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        kind = "file"
    else:
        if repo is None or baseline is None:
            raise InspectionError("missing historical baseline content")
        rel = target.relative_to(repo).as_posix()
        data, cut = _git(repo, ["show", "--no-ext-diff", "--no-textconv", f"{baseline}:{rel}"], max_bytes)
        if cut:
            raise InspectionError("historical content exceeds max_bytes")
        kind = "deleted"
    if len(data) > max_bytes:
        raise InspectionError("content exceeds max_bytes")
    if b"\0" in data:
        raise InspectionError("binary content cannot be reviewed as text")
    try:
        data.decode("utf-8")
    except UnicodeError as exc:
        raise InspectionError("non-UTF8 content cannot be reviewed as text") from exc
    return data, kind


def build_worklist(workspace, code_files, *, step, ticket_id, attempt, mode="full",
                   related=None, scopes=None, baselines=None, max_files=200,
                   max_bytes=1048576):
    root = _workspace(workspace)
    if not isinstance(step, str) or step not in PHASES or mode not in ("full", "fast"):
        raise InspectionError("invalid step/mode")
    if not isinstance(ticket_id, str) or not ticket_id.strip() or not (_positive(attempt) or isinstance(attempt, str) and attempt.strip()):
        raise InspectionError("invalid ticket_id/attempt")
    if not _positive(max_files) or not _positive(max_bytes):
        raise InspectionError("budgets must be positive integers")
    seeds = _paths(root, code_files)
    if not seeds:
        raise InspectionError("code_files must contain mandatory seeds")
    related = _paths(root, [] if related is None else related)
    scopes = _paths(root, sorted({str(Path(s).parent) for s in seeds}) if scopes is None else scopes, directory=True)
    if not scopes or any(not _inside(s, scopes) for s in seeds):
        raise InspectionError("scopes must contain all code_files")
    if baselines is not None and not isinstance(baselines, dict):
        raise InspectionError("baselines must be a per-repository object")
    baselines = {_path(root, k, directory=True): v for k, v in (baselines or {}).items()}
    report = dict(schema_version=1, workspace=str(root), step=step, ticket_id=ticket_id,
                  attempt=attempt, mode=mode, required_phases=["reverse"] if step == "deepcheck" and mode == "fast" else list(PHASES[step]),
                  code_files=seeds, related=related, scopes=scopes, baselines=baselines,
                  resolved_baselines={}, max_files=max_files, max_bytes=max_bytes,
                  units=[], exclusions=[], unobserved=[], findings=[], coverage_status="complete_within_scope",
                  checks_version=CHECKS_VERSION, checks=[])
    debt, excluded = {}, {}
    required = {p: "code_files" for p in seeds}
    for p in related:
        required.setdefault(p, "explicit_related")
    repos = sorted({r for p in seeds + related for r in [_repo(root, p)] if r is not None})
    if set(baselines) - {r.relative_to(root).as_posix() for r in repos}:
        raise InspectionError("baseline key is not an affected repository")

    def names(repo, args, *, required_scan=True):
        label = repo.relative_to(root).as_posix()
        data, cut = _git(repo, args, max_files * 4096)
        chunks = data.split(b"\0")[:-1]  # Ignore any truncated terminal filename.
        if required_scan and (cut or len(chunks) > max_files):
            debt[label] = "candidate budget truncated; remaining paths unobserved"
        return [os.fsdecode(c) for c in chunks[:max_files]]

    candidates = set()
    for repo in repos:
        label = repo.relative_to(root).as_posix()
        ref = baselines.get(label, "HEAD")
        if not isinstance(ref, str) or not ref or ref.startswith("-"):
            raise InspectionError(f"invalid baseline: {label}")
        try:
            oid, cut = _git(repo, ["rev-parse", "--verify", "--end-of-options", ref + "^{commit}"])
            if cut:
                raise InspectionError("baseline output truncated")
        except InspectionError as exc:
            raise InspectionError(f"baseline unavailable for {label}: {ref}: {exc}") from exc
        baseline = oid.decode("ascii").strip()
        report["resolved_baselines"][label] = baseline
        diff = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z", baseline, "--"]
        local_scopes = [os.path.relpath(root / s, repo) for s in scopes if _inside(s, [label]) or _inside(label, [s])]
        local_scopes = [s if not s.startswith("..") else "." for s in local_scopes]
        # Literal user scopes, plus fixed exclusions, keep ticket sidecars out
        # of enumeration itself (not merely filtered after consuming budgets).
        excluded_specs = [":(glob,exclude)**/.icode_output/**"]
        pathspecs = [f":(literal){s}" for s in local_scopes] + excluded_specs
        changed = set(names(repo, diff + pathspecs)) if local_scopes else set()
        untracked = set(names(repo, ["ls-files", "--others", "--exclude-standard", "-z", "--", *pathspecs])) if local_scopes else set()
        association_scopes = sorted(set(local_scopes) | {os.path.relpath((root / p).parent, repo)
            for p in related if _repo(root, p) == repo})
        association_specs = [f":(literal){s}" for s in association_scopes] + excluded_specs
        scoped = set(names(repo, ["ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *association_specs]))
        outside = set(names(repo, diff + excluded_specs, required_scan=False)) | set(names(repo,
            ["ls-files", "--others", "--exclude-standard", "-z", "--", *excluded_specs], required_scan=False))
        for local in sorted(changed | untracked | scoped | outside):
            path = (repo / local).relative_to(root).as_posix()
            # Ticket sidecars are never source candidates. Their normal growth
            # cannot invalidate or consume the budget of a source inspection.
            if ".icode_output" in Path(path).parts or ".git" in Path(path).parts:
                excluded[path] = "control_directory"
                continue
            if not _inside(path, scopes):
                if local in scoped:
                    try:
                        _path(root, path)
                        candidates.add(path)
                    except InspectionError as exc:
                        excluded[path] = str(exc)
                if local in outside:
                    excluded[path] = "out_of_scope"
                continue
            try:
                _path(root, path)
            except InspectionError as exc:
                excluded[path] = str(exc)
                if local in changed | untracked:
                    debt[path] = str(exc)
                continue
            if _repo(root, path) != repo:  # Never absorb a nested repository through its parent.
                excluded[path] = "different_repository"
                continue
            candidates.add(path)
            if local in changed | untracked:
                required.setdefault(path, "git_change")
    for seed in seeds + related:
        if _repo(root, seed) is None:
            debt[seed] = "no affected Git repository; diff/association coverage unavailable"
            parent = (root / seed).parent
            try:
                with os.scandir(parent) as entries:
                    local = []
                    for entry in entries:
                        if len(local) >= max_files:
                            debt[seed] = "no Git; association candidate budget truncated"
                            break
                        local.append(entry.name)
                for name in sorted(local):
                    path = (parent / name).relative_to(root).as_posix()
                    try:
                        _path(root, path)
                    except InspectionError as exc:
                        excluded[path] = str(exc)
                        continue
                    if (root / path).is_file():
                        candidates.add(path)
            except OSError as exc:
                debt[seed] = f"no Git; association directory unreadable: {exc}"
        for path in sorted(candidates):
            if Path(path).suffix.lower() in ASSOCIATED_SUFFIXES and _stem(path) == _stem(seed) and _repo(root, path) == _repo(root, seed):
                required.setdefault(path, "same_stem_or_test")
    groups, source_groups = {}, {}
    ordered = seeds + sorted(set(required) - set(seeds))
    for index, path in enumerate(ordered):
        if index >= max_files:
            debt[path] = "required file exceeds max_files budget"
            continue
        repo = _repo(root, path)
        label = repo.relative_to(root).as_posix() if repo else None
        try:
            data, kind = _content(root, path, repo, report["resolved_baselines"].get(label), max_bytes)
        except (InspectionError, OSError) as exc:
            debt[path] = str(exc)
            continue
        groups.setdefault((label, _stem(path)), []).append(dict(path=path, sha256=hashlib.sha256(data).hexdigest(), kind=kind, reason=required[path], rules=_rules(path), reads={}))
        if Path(path).suffix.lower() in SOURCE_SUFFIXES:
            text = _trigger_content(root, path, data, repo, report["resolved_baselines"].get(label), max_bytes, debt)
            kinds = set()
            if TIMESTAMP_SIGNAL.search(text) or INTEGER_CONVERSION.search(text):
                kinds.add("timestamp_contract")
            if SHARED_SIGNAL.search(text):
                kinds.add("shared_variant_consumers")
            source_groups.setdefault(label, {})[path] = kinds
    for key in sorted(groups, key=str):
        members = sorted(groups[key], key=lambda f: f["path"])
        unit_id = hashlib.sha256("\0".join(f["path"] for f in members).encode()).hexdigest()
        report["units"].append(dict(unit_id=unit_id, files=members))
    report["exclusions"] = [dict(path=p, reason=r) for p, r in sorted(excluded.items())]
    report["checks"] = _derive_checks(source_groups)
    report["unobserved"] = [dict(path=p, debt_reason=r) for p, r in sorted(debt.items())]
    if debt:
        report["coverage_status"] = "partial"
    return report


def _rebuild(report, workspace, code_files=None):
    if not isinstance(report, dict):
        raise InspectionError("report must be an object")
    keys = ("code_files", "step", "ticket_id", "attempt", "mode", "related", "scopes", "baselines", "max_files", "max_bytes")
    if any(k not in report for k in keys):
        raise InspectionError("report missing identity/scope/budget fields")
    allowed = set(keys) | {"schema_version", "workspace", "required_phases", "resolved_baselines", "units", "exclusions", "unobserved", "findings", "coverage_status", "debt_reason", "checks_version", "checks"}
    if set(report) - allowed:
        raise InspectionError("unknown report fields")
    pinned = report.get("resolved_baselines")
    if not isinstance(pinned, dict) or any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{40,64}", v) for v in pinned.values()):
        raise InspectionError("invalid pinned baseline identities")
    # A round binds the resolved commit, not the continued existence of a
    # symbolic branch/tag. No fallback to a different HEAD is permitted.
    options = {k: report[k] for k in keys if k not in ("code_files", "baselines")}
    expected = build_worklist(workspace, report["code_files"] if code_files is None else code_files,
                              baselines=pinned, **options)
    expected["baselines"] = report["baselines"]
    return expected


def _flatten(report):
    if not isinstance(report, dict) or not isinstance(report.get("units"), list):
        raise InspectionError("units must be an array")
    result = {}
    for unit in report["units"]:
        if not isinstance(unit, dict) or set(unit) != {"unit_id", "files"} or not isinstance(unit.get("unit_id"), str) or not isinstance(unit.get("files"), list) or not unit["files"]:
            raise InspectionError("invalid unit")
        for f in unit["files"]:
            if not isinstance(f, dict) or set(f) != {"path", "sha256", "kind", "reason", "rules", "reads"} or not isinstance(f.get("path"), str) or f["path"] in result:
                raise InspectionError("invalid/duplicate file entry")
            result[f["path"]] = f
    return result


def _assessment_issues(assessment, check, report, root, required, *, allow_unfinished=False, cache=None):
    """Validate each explicit cell and its source/artifact evidence without executing tests."""
    if not isinstance(assessment, dict) or set(assessment) != {"cells"} or not isinstance(assessment["cells"], dict):
        return ["assessment must contain only cells"]
    cells = assessment["cells"]
    issues, cache = [], {} if cache is None else cache
    if set(cells) != set(check["required_items"]):
        issues.append("assessment required cells differ")
    for item, cell in cells.items():
        if not isinstance(cell, dict) or set(cell) != {"status", "reason", "evidence"}:
            issues.append(f"invalid assessment cell: {item}")
            continue
        status = cell["status"]
        if status not in ("handled", "pass", "not_applicable", "pending", "fail"):
            issues.append(f"invalid assessment status: {item}")
        elif status in ("pending", "fail") and not allow_unfinished:
            issues.append(f"unfinished assessment: {item}")
        if not isinstance(cell["reason"], str) or not cell["reason"].strip():
            issues.append(f"assessment reason required, including not_applicable: {item}")
        refs = cell["evidence"]
        if not isinstance(refs, list) or not refs:
            issues.append(f"assessment evidence required: {item}")
            continue
        for ref in refs:
            try:
                if not isinstance(ref, dict):
                    raise InspectionError("evidence must be an object")
                kind = ref.get("kind")
                path = _path(root, ref.get("path"), artifact=kind in ("test", "simulation", "runtime"))
                if kind == "source":
                    fields = {"kind", "path", "start_line", "end_line", "source_sha256", "excerpt"}
                    if set(ref) != fields or path not in check["paths"] or path not in required:
                        raise InspectionError("source evidence must locate a current check member")
                    if (kind, path) not in cache:
                        repo = _repo(root, path)
                        label = repo.relative_to(root).as_posix() if repo else None
                        data, _ = _content(root, path, repo, report["resolved_baselines"].get(label), report["max_bytes"])
                        cache[kind, path] = (hashlib.sha256(data).hexdigest(), data.decode("utf-8").splitlines(keepends=True))
                    digest, lines = cache[kind, path]
                    start, end = ref["start_line"], ref["end_line"]
                    if not _positive(start) or not _positive(end) or end < start or end > len(lines):
                        raise InspectionError("invalid source evidence line range")
                    if ref["source_sha256"] != digest or digest != required[path]["sha256"] or ref["excerpt"] != "".join(lines[start - 1:end]):
                        raise InspectionError("source evidence hash/excerpt drift")
                elif kind in ("test", "simulation", "runtime"):
                    if set(ref) != {"kind", "path", "sha256", "simulated", "evidence_boundary"}:
                        raise InspectionError("artifact evidence missing identity/boundary")
                    if type(ref["simulated"]) is not bool or ref["simulated"] != (kind == "simulation"):
                        raise InspectionError("simulation must be explicitly identified")
                    if not isinstance(ref["evidence_boundary"], str) or not ref["evidence_boundary"].strip():
                        raise InspectionError("artifact evidence requires a nonempty boundary")
                    if (kind, path) not in cache:
                        data, _ = _content(root, path, None, None, report["max_bytes"], artifact=True)
                        cache[kind, path] = hashlib.sha256(data).hexdigest()
                    if ref["sha256"] != cache[kind, path]:
                        raise InspectionError("artifact evidence hash drift")
                else:
                    raise InspectionError("unknown evidence kind")
            except (ValueError, OSError, TypeError, UnicodeError) as exc:
                issues.append(f"invalid assessment evidence {item}: {exc}")
    return issues


def _checks_issues(report, expected, root, required, *, required_checks_version=None, allow_unfinished=False):
    if required_checks_version is not None and (type(required_checks_version) is not int or required_checks_version != CHECKS_VERSION):
        return ["unsupported required_checks_version"]
    present = "checks_version" in report or "checks" in report
    if not present and required_checks_version is None:
        return []  # Genuine old v1 records can be explained read-only.
    if type(report.get("checks_version")) is not int or report.get("checks_version") != CHECKS_VERSION:
        return ["checks_version missing/unsupported; marked attempts cannot downgrade"]
    checks = report.get("checks")
    if not isinstance(checks, list) or any(not isinstance(c, dict) or set(c) != set(CHECK_IDENTITY_KEYS) | {"results"} for c in checks):
        return ["invalid checks shape"]
    declared, derived = check_declarations(report), check_declarations(expected)
    if declared != derived or any(type(c[key]) is not type(target[key])
                                  for c, target in zip(declared, derived) for key in CHECK_IDENTITY_KEYS):
        return ["special checks derived identity drift"]
    issues, cache = [], {}
    for check in checks:
        results = check["results"]
        if not isinstance(results, dict) or set(results) - set(expected["required_phases"]):
            issues.append(f"invalid assessment phases: {check['kind']}")
            continue
        for phase in expected["required_phases"]:
            if phase not in results:
                if not allow_unfinished:
                    issues.append(f"missing assessment: {check['kind']}:{phase}")
                continue
            issues.extend(f"{check['kind']}:{phase}:{issue}" for issue in _assessment_issues(
                results[phase], check, report, root, required, allow_unfinished=allow_unfinished, cache=cache))
    return issues


def apply_assessment(report, workspace, check_id, phase, assessment):
    """Record one complete, independently assessed phase after its actual Read.

    This shared API mutates only after successful validation. Callers retain
    their own attempt/round lock, before_write and artifact receipt transaction.
    Neither a handled record nor a lexical trigger proves semantic correctness.
    """
    pending = dict(report, coverage_status="partial", debt_reason="pending assessment")
    issues = validate_worklist(pending, workspace, allow_incomplete=True, required_checks_version=CHECKS_VERSION)
    if issues:
        raise InspectionError(str(issues))
    selected = [c for c in report["checks"] if c["check_id"] == check_id]
    if len(selected) != 1 or phase not in report["required_phases"]:
        raise InspectionError("assessment requires a unique check_id and required phase")
    check = selected[0]
    required = _flatten(report)
    if any(required[path]["reads"].get(phase) != required[path]["sha256"] for path in check["paths"]):
        raise InspectionError("assessment requires current Read for every check member in this phase")
    issues = _assessment_issues(assessment, check, report, _workspace(workspace), required, allow_unfinished=True)
    if issues:
        raise InspectionError(str(issues))
    check["results"][phase] = copy.deepcopy(assessment)
    return report


def validate_worklist(report, workspace, *, code_files=None, step=None,
                      ticket_id=None, attempt=None, allow_incomplete=False, required_checks_version=None):
    try:
        expected = _rebuild(report, workspace, code_files)
        actual, required = _flatten(report), _flatten(expected)
        issues = []
        for key in ("schema_version", "workspace", "step", "ticket_id", "attempt", "mode", "required_phases", "code_files", "related", "scopes", "baselines", "resolved_baselines", "max_files", "max_bytes"):
            if type(report.get(key)) is not type(expected[key]) or report.get(key) != expected[key]:
                issues.append(f"identity/scope drift: {key}")
        for key, value in (("step", step), ("ticket_id", ticket_id), ("attempt", attempt)):
            if value is not None and (type(report.get(key)) is not type(value) or report.get(key) != value):
                issues.append(f"identity mismatch: {key}")
        if set(actual) != set(required):
            issues.append("required files differ from independently derived scope")
        if [(u["unit_id"], [f["path"] for f in u["files"]]) for u in report["units"]] != [(u["unit_id"], [f["path"] for f in u["files"]]) for u in expected["units"]]:
            issues.append("unit grouping/id drift")
        status = report.get("coverage_status")
        if not isinstance(report.get("exclusions"), list) or any(not isinstance(e, dict) or set(e) != {"path", "reason"}
            or not all(isinstance(e[k], str) and e[k].strip() for k in ("path", "reason")) for e in report["exclusions"]):
            issues.append("invalid exclusions")
        if not isinstance(report.get("findings"), list) or any(not isinstance(f, dict) for f in report["findings"]):
            issues.append("invalid findings")
        unobserved = report.get("unobserved")
        if not isinstance(unobserved, list) or any(not isinstance(d, dict) or set(d) != {"path", "debt_reason"} or not isinstance(d.get("path"), str) or not d["path"].strip() or not isinstance(d.get("debt_reason"), str) or not d["debt_reason"].strip() for d in unobserved):
            return issues + ["invalid unobserved debt"]
        if any(d not in unobserved for d in expected["unobserved"]):
            issues.append("derived unobserved debt omitted")
        global_debt = report.get("debt_reason", "")
        if not isinstance(global_debt, str):
            issues.append("invalid debt_reason")
            global_debt = ""
        elif "debt_reason" in report and not global_debt.strip():
            issues.append("empty debt_reason")
        incomplete = status in ("partial", "degraded") and bool(unobserved or global_debt.strip())
        if status not in ("complete_within_scope", "partial", "degraded") or status == "complete_within_scope" and (unobserved or global_debt.strip()):
            issues.append("invalid coverage_status/debt combination")
        if status != "complete_within_scope" and not (allow_incomplete and incomplete):
            issues.append("inspection incomplete without accepted explicit debt")
        issues.extend(_checks_issues(report, expected, _workspace(workspace), required,
            required_checks_version=required_checks_version, allow_unfinished=allow_incomplete and incomplete))
        debt_paths = {d["path"] for d in unobserved}
        for path in sorted(set(actual) & set(required)):
            f, target = actual[path], required[path]
            for key in ("sha256", "kind", "reason"):
                if f.get(key) != target[key]:
                    issues.append(f"file {key} drift: {path}")
            if not isinstance(f.get("rules"), list) or any(r not in f["rules"] for r in target["rules"]) or any(not isinstance(r, str) or not r for r in f.get("rules", [])):
                issues.append(f"required rules missing: {path}")
            reads = f.get("reads")
            if not isinstance(reads, dict):
                issues.append(f"invalid reads: {path}")
                continue
            for phase, digest in reads.items():
                if phase not in expected["required_phases"] or digest != target["sha256"]:
                    issues.append(f"Read hash/phase drift: {path}:{phase}")
            for phase in expected["required_phases"]:
                if phase not in reads and not (allow_incomplete and incomplete and (path in debt_paths or global_debt.strip())):
                    issues.append(f"missing Read: {path}:{phase}")
        return issues
    except (ValueError, OSError, TypeError, UnicodeError) as exc:
        return [f"invalid worklist: {exc}"]


def validate_findings(findings, report, workspace):
    try:
        root = _workspace(workspace)
        expected = _rebuild(report, root)
        actual, required = _flatten(report), _flatten(expected)
        if report.get("workspace") != str(root) or set(actual) != set(required):
            raise InspectionError("finding worklist scope/workspace drift")
        if not isinstance(findings, list):
            raise InspectionError("findings must be an array")
        issues, seen = [], set()
        for finding in findings:
            if not isinstance(finding, dict):
                issues.append("finding must be an object")
                continue
            fid = finding.get("finding_id", finding.get("id"))
            if not isinstance(fid, str) or not fid.strip() or fid in seen:
                issues.append("missing/duplicate finding_id")
                continue
            seen.add(fid)
            if "verification_status" in finding and finding["verification_status"] not in ("confirmed", "needs_more_evidence"):
                issues.append(f"invalid verification_status: {fid}")
            if "evidence_boundary" in finding and (not isinstance(finding["evidence_boundary"], str) or not finding["evidence_boundary"].strip()):
                issues.append(f"invalid evidence_boundary: {fid}")
            confirmed = finding.get("verification_status") == "confirmed"
            if finding.get("status") == "resolved" and not confirmed:
                continue  # Historical locations are not current-round evidence.
            locations = finding.get("locations")
            boundary = finding.get("evidence_boundary")
            if locations is None or locations == []:
                nonsource = locations == [] and finding.get("category") in ("design", "log", "logs", "documentation", "process")
                if not isinstance(boundary, str) or not boundary.strip() or not nonsource and (confirmed or finding.get("verification_status") != "needs_more_evidence"):
                    issues.append(f"unlocated finding requires evidence boundary/needs_more_evidence: {fid}")
                continue
            if not isinstance(locations, list):
                issues.append(f"locations must be an array: {fid}")
                continue
            for loc in locations:
                try:
                    if not isinstance(loc, dict) or set(loc) != {"path", "start_line", "end_line", "source_sha256", "excerpt"}:
                        raise InspectionError("source location missing required fields")
                    path = _path(root, loc["path"])
                    if path not in actual or actual[path].get("sha256") != required[path]["sha256"]:
                        raise InspectionError("location not in current worklist")
                    start, end = loc["start_line"], loc["end_line"]
                    if not _positive(start) or not _positive(end) or end < start:
                        raise InspectionError("invalid 1-based line range")
                    repo = _repo(root, path)
                    label = repo.relative_to(root).as_posix() if repo else None
                    data, _ = _content(root, path, repo, expected["resolved_baselines"].get(label), expected["max_bytes"])
                    if loc["source_sha256"] != hashlib.sha256(data).hexdigest():
                        raise InspectionError("source_sha256 drift")
                    lines = data.decode("utf-8").splitlines(keepends=True)
                    if end > len(lines) or loc["excerpt"] != "".join(lines[start - 1:end]):
                        raise InspectionError("excerpt must exactly match the complete line segment")
                except (ValueError, OSError, TypeError) as exc:
                    issues.append(f"invalid source location {fid}: {exc}")
        return issues
    except (ValueError, OSError, TypeError, UnicodeError) as exc:
        return [f"invalid findings: {exc}"]
