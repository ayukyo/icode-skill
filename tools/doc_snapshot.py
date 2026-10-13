#!/usr/bin/env python3
"""Capture, publish, bind and resolve DOC snapshots (Python 3.9+ and Git)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import subprocess
import sys
import tempfile

HASH_CHUNK_BYTES = 1024 * 1024
IDENTITY_SCHEMA = 1
STORAGE_SCHEMA = 2
HEX_ID = re.compile(r"[0-9a-f]{64}\Z")
CHAPTER = re.compile(r"[\w.-]+\.md\Z", re.UNICODE)
WINDOWS_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])\Z", re.IGNORECASE)


class SnapshotError(ValueError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _file_sha(path):
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise SnapshotError(f"input changed while hashing: {path}")
    return digest.hexdigest()


def module_key(url, branch):
    if not isinstance(url, str) or not url or not isinstance(branch, str) or not branch:
        raise SnapshotError("URL and effective branch are required")
    basename = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    if basename.endswith(".git"):
        basename = basename[:-4]
    basename = re.sub(r"[^A-Za-z0-9_-]", "_", basename) or "module"
    return basename + "_" + hashlib.sha256((url + ":" + branch).encode()).hexdigest()[:12]


def _git(repo, args, executable, *, optional=False):
    command = [executable, "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(repo), *args]
    result = subprocess.run(command, capture_output=True, timeout=60, shell=False)
    if result.returncode and not optional:
        raise SnapshotError(f"Git read failed ({args[0]}, exit {result.returncode})")
    return result.stdout if result.returncode == 0 else None


def _source_path(root, raw):
    name = os.fsdecode(raw).rstrip("/")
    posix = PurePosixPath(name)
    if not name or posix.is_absolute() or ".." in posix.parts or "\\" in name:
        raise SnapshotError("invalid Git source path")
    path = root.joinpath(*posix.parts)
    if os.name == "nt" and (":" in name or path.drive != root.drive):
        raise SnapshotError("invalid Windows Git source path")
    if not path.parent.resolve().is_relative_to(root):
        raise SnapshotError("source path parent escapes repository")
    return name, path


def _source_files(root, executable, seen):
    if root in seen:
        raise SnapshotError("recursive repository input")
    seen = seen | {root}
    indexed = {}
    for record in _git(root, ["ls-files", "--stage", "-z"], executable).split(b"\0"):
        if not record:
            continue
        header, separator, raw = record.partition(b"\t")
        fields = header.split()
        if not separator or len(fields) != 3:
            raise SnapshotError("invalid Git index output")
        entry = indexed.setdefault(raw, {"modes": set(), "gitlink": None})
        entry["modes"].add(fields[0].decode("ascii"))
        if fields[0] == b"160000":
            entry["gitlink"] = fields[1].decode("ascii")
    for raw in _git(root, ["ls-files", "--others", "--exclude-standard", "-z"], executable).split(b"\0"):
        if raw:
            indexed.setdefault(raw, {"modes": set(), "gitlink": None})
    files = []
    for raw, index in sorted(indexed.items()):
        name, path = _source_path(root, raw)
        item = {"path": name, "git_modes": sorted(index["modes"])}
        if path.is_symlink():
            item.update(kind="symlink", sha256=hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest())
        elif index["gitlink"] or path.is_dir():
            item.update(kind="repository", commit=index["gitlink"], available=False)
            if (path / ".git").exists():
                actual_root = _git(path, ["rev-parse", "--show-toplevel"], executable, optional=True)
                if actual_root and Path(os.fsdecode(actual_root.strip())).resolve() == path.resolve():
                    child_head = _git(path, ["rev-parse", "--verify", "HEAD"], executable, optional=True)
                    child_files = _source_files(path.resolve(), executable, seen)
                    item.update(available=True, commit=child_head.decode().strip() if child_head else None,
                                source_fingerprint=_digest(child_files), files=child_files)
        elif path.is_file():
            item.update(kind="file", sha256=_file_sha(path))
        elif not path.exists():
            item.update(kind="missing")
        else:
            raise SnapshotError(f"unsupported source input: {name}")
        files.append(item)
    return files


def _dependency_bindings(dependencies):
    roles = {}
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise SnapshotError("dependency identity must be a JSON object")
        role = dependency.get("input_role", dependency["module_key"])
        if not isinstance(role, str) or not role:
            raise SnapshotError("dependency input_role must be a nonempty string")
        version = (dependency["module_key"], dependency["source_id"])
        if role in roles and roles[role] != version:
            raise SnapshotError("distinct dependency versions require distinct explicit input_role values")
        roles[role] = version
    return sorted((role, *version) for role, version in roles.items())


def _source_id(identity):
    return _digest({"identity_schema": IDENTITY_SCHEMA, "source_mode": "working-tree",
                    "commit": identity["commit"], "source_fingerprint": identity["source_fingerprint"],
                    "dependencies": _dependency_bindings(identity.get("dependencies", []))})


def _validate_identity(identity):
    if not isinstance(identity, dict):
        raise SnapshotError("source identity must be a JSON object")
    try:
        if identity["identity_schema"] != IDENTITY_SCHEMA or identity["source_mode"] != "working-tree":
            raise SnapshotError("unsupported source identity")
        if (not isinstance(identity["repo_root"], str) or not identity["repo_root"]
                or not (PurePosixPath(identity["repo_root"]).is_absolute()
                        or PureWindowsPath(identity["repo_root"]).is_absolute())):
            raise SnapshotError("source repo_root must be an absolute path string")
        if identity["module_key"] != module_key(identity["url"], identity["branch"]):
            raise SnapshotError("module family identity mismatch")
        if identity["source_fingerprint"] != _digest(identity["files"]) or identity["source_id"] != _source_id(identity):
            raise SnapshotError("source manifest fingerprint mismatch")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", identity["commit"]):
            raise SnapshotError("a full valid HEAD is required")
        for dependency in identity.get("dependencies", []):
            _validate_identity(dependency)
    except (KeyError, TypeError) as error:
        raise SnapshotError("invalid source identity fields") from error


def capture(repo, url, branch=None, *, git_executable=None, dependencies=()):
    executable = git_executable or shutil.which("git")
    if not executable:
        raise SnapshotError("Git executable unavailable")
    root = Path(repo).expanduser().resolve(strict=True)
    actual = Path(os.fsdecode(_git(root, ["rev-parse", "--show-toplevel"], executable).strip())).resolve()
    if root != actual:
        raise SnapshotError("capture requires the resolved Git checkout root")
    head = _git(root, ["rev-parse", "--verify", "HEAD"], executable).decode().strip()
    checkout_branch = _git(root, ["symbolic-ref", "--short", "HEAD"], executable, optional=True)
    checkout_branch = checkout_branch.decode().strip() if checkout_branch else "(detached)"
    deps = list(dependencies)
    for dependency in deps:
        _validate_identity(dependency)
    _dependency_bindings(deps)
    files = _source_files(root, executable, set())
    # A per-file stat only detects changes during that one read. A second full
    # inventory/content pass also catches edits to earlier files and additions
    # made while later files were being read. It does not lock the worktree.
    if files != _source_files(root, executable, set()):
        raise SnapshotError("worktree changed while capturing the source manifest")
    if head != _git(root, ["rev-parse", "--verify", "HEAD"], executable).decode().strip():
        raise SnapshotError("HEAD changed while capturing")
    identity = {"identity_schema": IDENTITY_SCHEMA, "source_mode": "working-tree", "repo_root": str(root),
                "url": url, "branch": branch or checkout_branch, "checkout_branch": checkout_branch,
                "commit": head, "files": files, "source_fingerprint": _digest(files), "dependencies": deps}
    identity["module_key"] = module_key(url, identity["branch"])
    identity["source_id"] = _source_id(identity)
    return identity


def _fresh(identity, executable):
    deps = [_fresh(d, executable) for d in identity.get("dependencies", [])]
    current = capture(identity["repo_root"], identity["url"], identity["branch"],
                      git_executable=executable, dependencies=deps)
    if "input_role" in identity:
        current["input_role"] = identity["input_role"]
    return current


def _family(library, identity):
    _validate_identity(identity)
    root = Path(library).expanduser().resolve()
    family = root / identity["module_key"]
    for path in (family, family / "snapshots"):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise SnapshotError("snapshot namespace is not a real directory")
        if not path.resolve().is_relative_to(root):
            raise SnapshotError("snapshot namespace escapes library")
    return family


def _source_roots(identity):
    """Include actual source roots behind nested repositories and dependencies."""
    def nested(root, files):
        yield root
        for item in files:
            if item.get("kind") == "repository" and item.get("available"):
                child = root.joinpath(*PurePosixPath(item["path"]).parts).resolve()
                yield from nested(child, item["files"])
    yield from nested(Path(identity["repo_root"]).resolve(), identity["files"])
    for dependency in identity.get("dependencies", []):
        yield from _source_roots(dependency)


def _chapter_names(hashes):
    if not isinstance(hashes, dict) or not hashes:
        raise SnapshotError("review must bind a nonempty chapter manifest")
    for name, sha in hashes.items():
        if not isinstance(name, str) or not CHAPTER.fullmatch(name) or name.startswith("."):
            raise SnapshotError("chapters must be portable Markdown basenames")
        if WINDOWS_DEVICE.fullmatch(name.split(".", 1)[0]):
            raise SnapshotError("chapters must not use Windows device names")
        if not isinstance(sha, str) or not HEX_ID.fullmatch(sha):
            raise SnapshotError("invalid reviewed chapter SHA256")
    if len({name.casefold() for name in hashes}) != len(hashes):
        raise SnapshotError("chapter names collide on Windows")
    return sorted(hashes)


def _snapshot_id(source_id, template, scope, hashes, review):
    return _digest({"storage_schema": STORAGE_SCHEMA, "source_id": source_id,
                    "template_version": template, "scope": scope, "chapter_sha256": hashes,
                    "review_sha256": _digest(review)})


def _read_snapshot(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise SnapshotError("invalid snapshot directory")
    if directory.resolve().parent != directory.parent.resolve():
        raise SnapshotError("snapshot directory escapes its namespace")
    path = directory / "_meta.json"
    if path.is_symlink():
        raise SnapshotError("snapshot metadata must not be a symlink")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise SnapshotError("snapshot metadata must be a JSON object")
    return metadata


def resolve(library, identity, snapshot_id=None, *, template_version=None, git_executable=None):
    family = _family(library, identity)
    if snapshot_id is not None and (not isinstance(snapshot_id, str) or not HEX_ID.fullmatch(snapshot_id)):
        raise SnapshotError("snapshot_id must be a full SHA256 identifier")
    if _fresh(identity, git_executable)["source_id"] != identity["source_id"]:
        return {"status": "source_mismatch"}
    if snapshot_id is None:
        candidates = []
        for path in sorted((family / "snapshots").glob("*/_meta.json")):
            if not HEX_ID.fullmatch(path.parent.name):
                continue
            metadata = _read_snapshot(path.parent)
            if metadata.get("source_id") == identity["source_id"] and (template_version is None or metadata.get("template_version") == template_version):
                candidates.append(path.parent.name)
        if len(candidates) > 1:
            return {"status": "ambiguous", "snapshot_ids": candidates}
        if not candidates:
            return {"status": "legacy_summary_only" if (family / "00_overview.md").is_file() else "missing"}
        snapshot_id = candidates[0]
    directory = family / "snapshots" / snapshot_id
    if not directory.exists():
        return {"status": "missing"}
    metadata = _read_snapshot(directory)
    if metadata.get("source_id") != identity["source_id"] or metadata.get("module_key") != identity["module_key"]:
        return {"status": "source_mismatch"}
    if template_version is not None and metadata.get("template_version") != template_version:
        return {"status": "template_mismatch"}
    hashes = metadata.get("chapter_sha256", {})
    names = _chapter_names(hashes)
    recorded_identity = metadata.get("source_identity", {})
    _validate_identity(recorded_identity)
    if any(metadata.get(key) != recorded_identity.get(key) for key in ("source_id", "module_key", "commit", "source_fingerprint", "source_mode")):
        raise SnapshotError("snapshot source metadata mismatch")
    review = metadata.get("review", {})
    if (not isinstance(review, dict) or review.get("source_id") != identity["source_id"] or review.get("status") != "verified"
            or review.get("chapter_sha256") != hashes or review.get("scope") != metadata.get("scope")
            or review.get("template_version") != metadata.get("template_version") or not review.get("evidence")):
        raise SnapshotError("snapshot review metadata mismatch")
    expected_id = _snapshot_id(identity["source_id"], metadata.get("template_version"), metadata.get("scope"), hashes, review)
    if metadata.get("storage_schema") != STORAGE_SCHEMA or expected_id != snapshot_id or metadata.get("snapshot_id") != snapshot_id:
        raise SnapshotError("snapshot metadata identity mismatch")
    actual_names = sorted(p.name for p in directory.glob("*.md"))
    if actual_names != names or any((directory / name).is_symlink() or _file_sha(directory / name) != hashes[name] for name in names):
        return {"status": "modified"}
    if metadata.get("scope") != "full" or metadata.get("status") != "verified":
        return {"status": "partial", "source_id": identity["source_id"], "snapshot_id": snapshot_id}
    return {"status": "match", "source_id": identity["source_id"], "snapshot_id": snapshot_id,
            "directory": str(directory), "chapter_sha256": hashes, "template_version": metadata["template_version"]}


def publish(library, identity, stage, review, *, git_executable=None):
    family = _family(library, identity)
    if any(family.is_relative_to(root) for root in _source_roots(identity)):
        raise SnapshotError("snapshot library must be outside source inputs")
    if not isinstance(review, dict):
        raise SnapshotError("review must be a JSON object")
    if review.get("source_id") != identity["source_id"] or review.get("status") != "verified":
        raise SnapshotError("review does not attest the captured source")
    scope = review.get("scope")
    template = review.get("template_version")
    if scope not in ("full", "partial") or not isinstance(template, str) or not template or not review.get("evidence"):
        raise SnapshotError("review scope, template and evidence are required")
    hashes = review.get("chapter_sha256", {})
    names = _chapter_names(hashes)
    if scope == "full" and not {"00_overview.md", "90_glossary.md", "99_code_facts_audit.md"}.issubset(names):
        raise SnapshotError("complete module snapshot needs overview, glossary and audit")
    stage = Path(stage).resolve(strict=True)
    if sorted(p.name for p in stage.glob("*.md")) != names:
        raise SnapshotError("reviewed chapter set differs from staged chapter set")
    for name in names:
        if (stage / name).is_symlink() or _file_sha(stage / name) != hashes[name]:
            raise SnapshotError("staged chapter changed after review")
    if _fresh(identity, git_executable)["source_id"] != identity["source_id"]:
        raise SnapshotError("source changed during documentation; recapture and review")
    version = _snapshot_id(identity["source_id"], template, scope, hashes, review)
    parent = family / "snapshots"
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / version
    if target.exists():
        existing = resolve(library, identity, version, git_executable=git_executable)
        if existing["status"] not in ("match", "partial"):
            raise SnapshotError("existing snapshot has manual changes; preserve and merge into a new revision")
    else:
        temporary = Path(tempfile.mkdtemp(prefix=".staging-", dir=parent))
        try:
            for name in names:
                shutil.copyfile(stage / name, temporary / name)
                if _file_sha(temporary / name) != hashes[name]:
                    raise SnapshotError("staged chapter changed while copying")
            metadata = {"storage_schema": STORAGE_SCHEMA, "module_key": identity["module_key"],
                        "snapshot_id": version, "source_id": identity["source_id"], "source_mode": "working-tree",
                        "commit": identity["commit"], "source_fingerprint": identity["source_fingerprint"],
                        "source_identity": identity, "template_version": template, "scope": scope,
                        "status": "verified", "chapter_sha256": hashes, "review": review}
            with (temporary / "_meta.json").open("w", encoding="utf-8") as stream:
                json.dump(metadata, stream, ensure_ascii=True, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            if _fresh(identity, git_executable)["source_id"] != identity["source_id"]:
                raise SnapshotError("source changed before publication")
            try:
                os.replace(temporary, target)
            except OSError:
                if not target.is_dir() or resolve(library, identity, version, git_executable=git_executable)["status"] not in ("match", "partial"):
                    raise
        finally:
            # Only remove this operation's owned staging directory, after
            # containment verification. Published snapshots are never removed.
            if temporary.exists():
                if temporary.is_symlink() or not temporary.resolve().is_relative_to(parent.resolve()):
                    raise SnapshotError("staging cleanup containment failed")
                shutil.rmtree(temporary)
    return {"module_key": identity["module_key"], "source_id": identity["source_id"], "snapshot_id": version,
            "commit": identity["commit"], "source_fingerprint": identity["source_fingerprint"],
            "doc_path": "snapshots/" + version, "directory": str(target), "generated": scope == "full"}


def bind_dependency(library, identity, snapshot_id, metadata, project_root, module_path, name,
                    *, git_executable=None):
    """Bind an already readable snapshot, preserving parent metadata and baselines."""
    _validate_identity(identity)
    relative = PurePosixPath(module_path)
    if (not module_path or relative.is_absolute() or ".." in relative.parts
            or "\\" in module_path or ":" in module_path):
        raise SnapshotError("module path must be a portable project-relative path")
    project = Path(project_root).resolve(strict=True)
    source = Path(identity["repo_root"]).resolve(strict=True)
    if not project.is_dir() or project.joinpath(*relative.parts).resolve(strict=True) != source:
        raise SnapshotError("module binding does not describe the actual project source path")
    path = Path(metadata).absolute()
    if path.name != "_meta.json" or path.is_symlink():
        raise SnapshotError("parent metadata must be a real _meta.json file")
    path = path.resolve()
    if path.is_relative_to(project) or any(path.is_relative_to(root) for root in _source_roots(identity)):
        raise SnapshotError("parent metadata must be outside source inputs")
    if not isinstance(name, str) or not name:
        raise SnapshotError("module name is required")
    resolved = resolve(library, identity, snapshot_id, git_executable=git_executable)
    if resolved["status"] not in ("match", "partial"):
        raise SnapshotError("snapshot cannot be bound: " + resolved["status"])
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.parent / ".doc-binding.lock"
    try:
        lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise SnapshotError("parent binding busy; reread and retry") from error
    temporary = None
    try:
        original = path.read_bytes() if path.exists() else None
        parent = json.loads(original.decode("utf-8")) if original is not None else {}
        if not isinstance(parent, dict) or not isinstance(parent.get("module_deps", []), list):
            raise SnapshotError("parent metadata must contain an object and module_deps list")
        dependencies = parent.setdefault("module_deps", [])
        if any(not isinstance(d, dict) for d in dependencies):
            raise SnapshotError("parent module_deps entries must be objects")
        if any(not isinstance(d.get("path", ""), str) for d in dependencies):
            raise SnapshotError("parent module_deps path fields must be strings")
        normalized = relative.as_posix().rstrip("/") + "/"
        matches = []
        for dependency in dependencies:
            old_path = dependency.get("path", "")
            if not old_path:
                continue
            old_relative = PurePosixPath(old_path.replace("\\", "/"))
            if old_relative.is_absolute() or ".." in old_relative.parts or ":" in old_path:
                raise SnapshotError("legacy module path requires explicit repair")
            # Normalize old ./, separators and the actual filesystem path;
            # Windows Path comparison also respects its case-insensitive rules.
            if project.joinpath(*old_relative.parts).resolve() == source:
                matches.append(dependency)
        if len(matches) > 1:
            raise SnapshotError("duplicate parent bindings for the actual module path")
        entry = matches[0] if matches else {}
        entry.update(name=name, url=identity["url"], branch=identity["branch"], key=identity["module_key"],
                     commit=identity["commit"], source_mode=identity["source_mode"],
                     source_fingerprint=identity["source_fingerprint"], source_id=identity["source_id"],
                     snapshot_id=snapshot_id, path=normalized, doc_path="snapshots/" + snapshot_id,
                     generated=resolved["status"] == "match")
        if not matches:
            dependencies.append(entry)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".doc-binding-", suffix=".json", dir=path.parent)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(parent, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Our exclusive lock serializes cooperating writers. Compare bytes as
        # well, so an editor that ignores the lock is preserved on conflict.
        current = resolve(library, identity, snapshot_id, git_executable=git_executable)
        if current["status"] != resolved["status"]:
            raise SnapshotError("snapshot changed before parent binding")
        if path.is_symlink() or (path.read_bytes() if path.exists() else None) != original:
            raise SnapshotError("parent metadata changed; reread and merge before retry")
        os.replace(temporary, path)
        return entry
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        os.close(lock_fd)
        lock.unlink()


def can_resume(item, identity, resolved):
    _validate_identity(identity)
    return (item.get("status") in ("verified", "done") and resolved.get("status") == "match"
            and item.get("source_id") == identity["source_id"] == resolved.get("source_id")
            and item.get("snapshot_id") == resolved.get("snapshot_id"))


def cache_key(cwd, keywords, identity, bindings, *, template_version=None):
    _validate_identity(identity)
    if template_version is not None and (not isinstance(template_version, str) or not template_version):
        raise SnapshotError("cache template_version must be a nonempty string")
    return _digest({"cwd": str(Path(cwd).resolve()), "keywords": sorted(set(keywords)),
                    "source_id": identity["source_id"], "module_key": identity["module_key"],
                    "template_version": template_version,
                    "bindings": sorted((_digest(binding) for binding in bindings))})


def default_library_root():
    try:
        if os.name == "posix" and os.environ.get("HOME") == "":
            import pwd
            home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        else:
            home = Path.home()
        if not home.is_absolute():
            raise SnapshotError("system home must be absolute; use --library-root")
        return home / ".claude/icode_data/module_docs"
    except (KeyError, RuntimeError) as error:
        raise SnapshotError("system home unavailable; use --library-root") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-executable", help="Git binary override; no shell commands are evaluated")
    commands = parser.add_subparsers(dest="command", required=True)
    capture_parser = commands.add_parser("capture", help="Read actual tracked/untracked worktree inputs to JSON")
    capture_parser.add_argument("--repo", required=True)
    capture_parser.add_argument("--url", required=True)
    capture_parser.add_argument("--branch")
    capture_parser.add_argument("--dependency-identity", action="append", default=[],
                                help="Identity JSON path, or ROLE=path for distinct provider roles")
    for name in ("publish", "resolve", "bind"):
        command = commands.add_parser(name)
        command.add_argument("--identity", required=True, help="capture JSON file")
        command.add_argument("--library-root", help="Default: system home/.claude/icode_data/module_docs")
        if name == "publish":
            command.add_argument("--stage", required=True)
            command.add_argument("--review", required=True, help="independently prepared review JSON")
        elif name == "resolve":
            command.add_argument("--snapshot-id")
            command.add_argument("--template-version")
        else:
            command.add_argument("--snapshot-id", required=True)
            command.add_argument("--project-meta", required=True)
            command.add_argument("--project-root", required=True)
            command.add_argument("--path", required=True)
            command.add_argument("--name", required=True)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            dependencies = []
            for value in args.dependency_identity:
                role = None
                path = Path(value)
                if not path.is_file() and "=" in value:
                    role, value = value.split("=", 1)
                    path = Path(value)
                dependency = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(dependency, dict):
                    raise SnapshotError("dependency identity must be a JSON object")
                if role is not None:
                    dependency["input_role"] = role
                dependencies.append(dependency)
            result = capture(args.repo, args.url, args.branch, git_executable=args.git_executable, dependencies=dependencies)
        else:
            identity = json.loads(Path(args.identity).read_text(encoding="utf-8"))
            library = args.library_root or default_library_root()
            if args.command == "publish":
                review = json.loads(Path(args.review).read_text(encoding="utf-8"))
                result = publish(library, identity, args.stage, review, git_executable=args.git_executable)
            elif args.command == "resolve":
                result = resolve(library, identity, args.snapshot_id, template_version=args.template_version,
                                 git_executable=args.git_executable)
            else:
                result = bind_dependency(library, identity, args.snapshot_id, args.project_meta,
                                         args.project_root, args.path, args.name,
                                         git_executable=args.git_executable)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (SnapshotError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
