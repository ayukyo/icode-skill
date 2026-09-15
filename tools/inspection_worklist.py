"""Deterministic, read-only inspection declarations (stdlib only).

Hashes/reads never prove actual Read, understanding or semantic judgment. Rules
are review prompts, not lint results; project LIMIT rules remain authoritative.
No persistence, build, hooks, external diff drivers or LLM calls are performed.
"""
import hashlib
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time


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


def _path(root, raw, *, directory=False):
    if not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw:
        raise InspectionError("path must be a nonempty relative POSIX string")
    p = Path(raw)
    if p.is_absolute() or ".." in raw.split("/"):
        raise InspectionError(f"unsafe path: {raw}")
    for part in p.parts:
        lower = part.lower()
        if lower in FORBIDDEN or lower == ".env" or lower.startswith(".env.") or lower.startswith("id_rsa") or lower.startswith("id_ed25519") or lower in {"credentials", "credentials.json"} or lower.endswith((".pem", ".key", ".p12", ".pfx")):
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


def _git(repo, args, limit=8192):
    """Bound both stdout memory and elapsed time, even for ls-files/git show."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    command = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
               "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args]
    try:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env) as proc:
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


def _content(root, path, repo, baseline, max_bytes):
    target = root / _path(root, path)
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
                  units=[], exclusions=[], unobserved=[], findings=[], coverage_status="complete_within_scope")
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
    groups = {}
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
    for key in sorted(groups, key=str):
        members = sorted(groups[key], key=lambda f: f["path"])
        unit_id = hashlib.sha256("\0".join(f["path"] for f in members).encode()).hexdigest()
        report["units"].append(dict(unit_id=unit_id, files=members))
    report["exclusions"] = [dict(path=p, reason=r) for p, r in sorted(excluded.items())]
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
    allowed = set(keys) | {"schema_version", "workspace", "required_phases", "resolved_baselines", "units", "exclusions", "unobserved", "findings", "coverage_status", "debt_reason"}
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


def validate_worklist(report, workspace, *, code_files=None, step=None,
                      ticket_id=None, attempt=None, allow_incomplete=False):
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
