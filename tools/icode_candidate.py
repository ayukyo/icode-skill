"""Bounded, read-only final-source snapshots and receipt applicability.

No Git writes, external diff, hooks, filters or model calls. Snapshot hashes
establish source identity; only real successful step receipts establish review.
"""
import hashlib
import importlib.util
from functools import lru_cache
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import struct
import time

DEFAULT_LIMITS = {"max_files": 50000, "max_bytes": 512 * 1024 * 1024,
                  "max_git_bytes": 16 * 1024 * 1024, "timeout_seconds": 30}
REVIEW_STEPS = ("code", "deepcheck", "audit")
CONTROL_DIRS = (".icode_output",)
_LFS_POINTER_MAX_BYTES = 4096
_IO_REPARSE_TAG_LX_SYMLINK = 0xA000001D
_GIT_OBJECT_SIZE_MAX_DIGITS = 20
_LFS_POINTER_PATTERN = re.compile(
    rb"version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n")


class CandidateError(ValueError):
    """Identity cannot be established completely inside the resource boundary."""


def _is_candidate_link(info):
    return (stat.S_ISLNK(info.st_mode) or
            getattr(info, "st_reparse_tag", 0) == _IO_REPARSE_TAG_LX_SYMLINK)


def _path_is_candidate_link(path):
    try:
        return _is_candidate_link(path.lstat())
    except FileNotFoundError:
        return False


def _stat_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, getattr(info, "st_reparse_tag", 0))


def _parse_lx_symlink(raw):
    # LX_SYMLINK has a standard reparse header, version 2, then Linux path
    # bytes. Hash those bytes unchanged, including non-UTF-8 names.
    if len(raw) < 13 or len(raw) > 16384:
        raise CandidateError("invalid LX symlink buffer length")
    tag, size, reserved, version = struct.unpack_from("<IHHI", raw)
    if tag != _IO_REPARSE_TAG_LX_SYMLINK or reserved or version != 2 or size != len(raw) - 8 or b"\0" in raw[12:]:
        raise CandidateError("invalid LX symlink reparse data")
    return raw[12:]


def _read_lx_symlink(path, before):
    # Windows Python does not expose LX links through os.readlink. Open the
    # reparse point itself, never its target; deny concurrent writes/deletion.
    import ctypes
    from ctypes import wintypes as w
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, w.LPVOID, w.DWORD, w.LPVOID, w.DWORD,
                                      ctypes.POINTER(w.DWORD), w.LPVOID]
    kernel.DeviceIoControl.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    # OPEN_EXISTING, FILE_SHARE_READ, OPEN_REPARSE_POINT | BACKUP_SEMANTICS.
    handle = kernel.CreateFileW(str(path), 0, 1, None, 3, 0x00200000 | 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        # Windows fstat does not populate st_reparse_tag; the ioctl header
        # below verifies the tag on this same, identity-checked handle.
        if _stat_identity(os.fstat(descriptor))[:-1] != _stat_identity(before)[:-1]:
            raise CandidateError("LX symlink replaced while opening")
        buffer = ctypes.create_string_buffer(16384)
        returned = w.DWORD()
        # FSCTL_GET_REPARSE_POINT, bounded by MAXIMUM_REPARSE_DATA_BUFFER_SIZE.
        if not kernel.DeviceIoControl(handle, 0x000900A8, None, 0, buffer, len(buffer), ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return _parse_lx_symlink(buffer.raw[:returned.value])
    finally:
        os.close(descriptor)  # owns and closes the Windows handle exactly once


_INSPECTION = None


class CandidateRead(Enum):
    FACTS = "facts"
    TREE = "tree"
    INDEX = "index"
    OTHERS = "others"
    BLOB = "blob"
    LINEAR = "linear"


@dataclass(frozen=True)
class CandidateReadQuery:
    operation: CandidateRead
    oid: str = ""
    old_oid: str = ""
    kind: str = ""


@dataclass(frozen=True)
class CandidateReadResult:
    data: bytes
    exit_code: int
    truncated: bool
    error: str | None = None


class CandidateGitProvider(ABC):
    workspace: Path

    @abstractmethod
    def assert_context(self):
        """Validate this invocation; None means valid, not native proof here."""

    @abstractmethod
    def query(self, request: CandidateReadQuery, *, limit: int) -> CandidateReadResult:
        """Return bounded closed-read bytes; never accepts argv or paths."""


def _candidate_provider(root, provider, required, *, deadline=None):
    if deadline is not None and time.monotonic() > deadline:
        raise CandidateError("candidate provider deadline exceeded")
    if type(required) is not bool:
        raise CandidateError("invalid provider requirement")
    if provider is None:
        if required:
            raise CandidateError("host-bound candidate provider missing")
        return
    if not isinstance(provider, CandidateGitProvider) or provider.workspace != root:
        raise CandidateError("candidate provider workspace/type mismatch")
    if provider.assert_context() is not None:
        raise CandidateError("candidate context check did not complete")
    if deadline is not None and time.monotonic() > deadline:
        raise CandidateError("candidate provider deadline exceeded")


def _candidate_provider_name(name):
    if _paths([name]) != [name] or name == ".":
        raise CandidateError("noncanonical candidate provider path")
    if os.name == "nt":
        windows = PureWindowsPath(name)
        if windows.drive or windows.root or ":" in name:
            raise CandidateError("unsafe Windows candidate provider path")
        for part in windows.parts:
            if part.lower() == ".git" or PureWindowsPath(part).is_reserved() or part.endswith((" ", ".")) or \
                    any(ord(character) < 32 or character in '<>"|?*' for character in part):
                raise CandidateError("unsafe Windows candidate provider component")
    return name


def _candidate_provider_paths(values):
    if not isinstance(values, (tuple, list)):
        raise CandidateError("candidate paths must be an array")
    return _paths([_candidate_provider_name(name) for name in values])


def _candidate_read(root, provider, operation, *, oid="", old_oid="", kind="", limit, deadline=None):
    _candidate_provider(root, provider, True, deadline=deadline)
    if not isinstance(operation, CandidateRead) or type(limit) is not int or limit < 1:
        raise CandidateError("invalid candidate read operation/budget")
    if type(oid) is not str or type(old_oid) is not str or type(kind) is not str:
        raise CandidateError("invalid candidate read OID type")
    with_oid = operation in (CandidateRead.TREE, CandidateRead.BLOB, CandidateRead.LINEAR)
    if (with_oid and re.fullmatch(r"[0-9a-f]{40}", oid) is None) or (not with_oid and oid):
        raise CandidateError("invalid candidate read OID")
    if operation == CandidateRead.LINEAR:
        if re.fullmatch(r"[0-9a-f]{40}", old_oid) is None or old_oid == oid:
            raise CandidateError("invalid candidate proof endpoint")
    elif old_oid:
        raise CandidateError("unexpected candidate old OID")
    if (operation == CandidateRead.BLOB and kind not in ("size", "content")) or \
            (operation != CandidateRead.BLOB and kind):
        raise CandidateError("invalid candidate read kind")
    result = provider.query(CandidateReadQuery(operation, oid, old_oid, kind), limit=limit)
    if not isinstance(result, CandidateReadResult) or type(result.data) is not bytes or \
            type(result.exit_code) is not int or type(result.truncated) is not bool or \
            result.error is not None or result.exit_code != 0 or result.truncated or len(result.data) > limit:
        raise CandidateError("candidate provider failed/incomplete")
    _candidate_provider(root, provider, True, deadline=deadline)
    data = result.data
    if operation in (CandidateRead.TREE, CandidateRead.INDEX, CandidateRead.OTHERS):
        if data and not data.endswith(b"\0"):
            raise CandidateError("unterminated candidate manifest")
        names = []
        try:
            for row in data.split(b"\0")[:-1]:
                if operation == CandidateRead.OTHERS:
                    raw_path = row
                else:
                    info, raw_path = row.split(b"\t", 1)
                    mode, middle, third = info.decode("ascii").split()
                    if mode not in ("100644", "100755", "120000"):
                        raise CandidateError("unsupported candidate provider mode/gitlink")
                    blob_oid = third if operation == CandidateRead.TREE else middle
                    if re.fullmatch(r"[0-9a-f]{40}", blob_oid) is None:
                        raise CandidateError("invalid manifest blob OID")
                    if (operation == CandidateRead.TREE and middle != "blob") or \
                            (operation == CandidateRead.INDEX and third != "0"):
                        raise CandidateError("invalid tree kind/unmerged index")
                name = raw_path.decode("utf-8")
                names.append(_candidate_provider_name(name))
        except (UnicodeError, ValueError) as error:
            raise CandidateError("invalid candidate manifest") from error
        if len(set(names)) != len(names):
            raise CandidateError("duplicate candidate manifest path")
    elif operation == CandidateRead.BLOB and kind == "size":
        if re.fullmatch(rb"0|[1-9][0-9]{0,19}", data) is None:
            raise CandidateError("invalid candidate blob size")
    elif operation == CandidateRead.BLOB:
        actual = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
        if actual != oid:
            raise CandidateError("candidate blob OID mismatch")
    elif operation == CandidateRead.LINEAR:
        try:
            records = data.decode("ascii").splitlines()
            commits = []
            for record in records:
                parts = record.split()
                if len(parts) != 2 or any(re.fullmatch(r"[0-9a-f]{40}", x) is None for x in parts):
                    raise CandidateError("invalid linear parent record")
                commits.append(parts[0])
            if not records or len(records) > 64 or len(set(commits)) != len(commits) or old_oid in commits:
                raise CandidateError("invalid/budget-exceeded candidate proof")
        except UnicodeError as error:
            raise CandidateError("invalid linear proof bytes") from error
    if deadline is not None and time.monotonic() > deadline:
        raise CandidateError("candidate provider deadline exceeded")
    return data


def _candidate_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise CandidateError("duplicate candidate facts key")
        value[key] = item
    return value


def _candidate_facts(root, provider, limit, *, deadline=None):
    raw = _candidate_read(root, provider, CandidateRead.FACTS, limit=limit, deadline=deadline)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_candidate_json_object)
        identity_keys = {"common_git_dir", "checkout_realpath", "object_format", "common_git_device",
                         "common_git_inode", "checkout_device", "checkout_inode"}
        if type(value) is not dict or set(value) != {"identity", "head", "head_tree", "base"}:
            raise CandidateError("invalid candidate facts shape")
        identity = value["identity"]
        if type(identity) is not dict or set(identity) != identity_keys or identity["object_format"] != "sha1":
            raise CandidateError("invalid candidate facts identity")
        if any(type(identity[k]) is not str or not Path(identity[k]).is_absolute() for k in
               ("common_git_dir", "checkout_realpath")) or identity["checkout_realpath"] != str(root):
            raise CandidateError("candidate facts root mismatch")
        if any(type(identity[k]) is not int or identity[k] < 0 for k in
               ("common_git_device", "common_git_inode", "checkout_device", "checkout_inode")):
            raise CandidateError("invalid candidate facts stat")
        actual = root.stat()
        if (identity["checkout_device"], identity["checkout_inode"]) != (actual.st_dev, actual.st_ino):
            raise CandidateError("candidate CODE identity changed")
        if any(type(value[k]) is not str or re.fullmatch(r"[0-9a-f]{40}", value[k]) is None
               for k in ("head", "head_tree", "base")):
            raise CandidateError("invalid candidate facts commit")
    except (UnicodeError, TypeError, ValueError, KeyError) as error:
        raise CandidateError("invalid candidate facts") from error
    if deadline is not None and time.monotonic() > deadline:
        raise CandidateError("candidate provider deadline exceeded")
    return value


def _git(root, args, limit, *, input_data=None):
    global _INSPECTION
    if _INSPECTION is None:
        spec = importlib.util.spec_from_file_location(
            "candidate_inspection_git", Path(__file__).with_name("inspection_worklist.py"))
        _INSPECTION = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_INSPECTION)
    try:
        if input_data is None:
            data, truncated = _INSPECTION._git(root, args, limit)
        else:
            data, truncated = _INSPECTION._git(root, args, limit, input_data=input_data)
    except _INSPECTION.InspectionError as exc:
        raise CandidateError(str(exc)) from exc
    if truncated:
        raise CandidateError("candidate Git scan exceeds max_git_bytes (no truncation allowed)")
    return data


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode()).hexdigest()


_SNAPSHOT_MEMO = None


def snapshot_identity_ok(snapshot):
    """Offline closed-shape/hash check; never caches live repository facts."""
    global _SNAPSHOT_MEMO
    if _SNAPSHOT_MEMO is None:
        spec = importlib.util.spec_from_file_location(
            "candidate_validation_memo", Path(__file__).with_name("validation_memo.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _SNAPSHOT_MEMO = module.PureJsonMemo(
            lambda value, rules: _snapshot_identity_ok(value, control_dirs=rules["control_dirs"]))
    return _SNAPSHOT_MEMO.check(snapshot, {"control_dirs": list(CONTROL_DIRS)})


def _snapshot_identity_ok(snapshot, *, control_dirs):
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
        if snapshot["control_artifact_exclusions"] != list(control_dirs) or \
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


@lru_cache(maxsize=8192)
def _lexical_candidate_path(raw, path_class):
    """Only lexical platform Path semantics; no filesystem or CWD observation."""
    value = path_class(raw)
    if value.is_absolute() or ".." in value.parts or ".git" in value.parts:
        raise CandidateError(f"unsafe candidate path: {raw}")
    return value.as_posix()


def _paths(values):
    if not isinstance(values, (tuple, list)):
        raise CandidateError("candidate paths must be an array")
    result = set()
    for raw in values:
        if not isinstance(raw, str) or not raw or "\0" in raw or "\\" in raw:
            raise CandidateError("candidate path must be a relative POSIX string")
        if type(raw) is str:
            normalized = _lexical_candidate_path(raw, Path)
        else:
            # Preserve custom str-subclass behavior without cache aliasing.
            value = Path(raw)
            if value.is_absolute() or ".." in value.parts or ".git" in value.parts:
                raise CandidateError(f"unsafe candidate path: {raw}")
            normalized = value.as_posix()
        result.add(normalized)
    return sorted(result)


def _excluded(path, exclusions):
    return any(path == x or path.startswith(x + "/") or
               (x in CONTROL_DIRS and x in Path(path).parts) for x in exclusions)


def repository_head(workspace, *, git_provider=None, require_git_provider=False):
    """Return an existing checkout's HEAD, or None for a non-Git/unborn fixture."""
    root = Path(workspace).resolve()
    deadline = (time.monotonic() + DEFAULT_LIMITS["timeout_seconds"] if git_provider is not None else None)
    _candidate_provider(root, git_provider, require_git_provider, deadline=deadline)
    if git_provider is not None:
        return _candidate_facts(root, git_provider, 8192, deadline=deadline)["head"]
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


def _apply_lfs_pointer(row, oid, pointer):
    match = _LFS_POINTER_PATTERN.fullmatch(pointer)
    if match and match[1].decode() == row["sha256"] and int(match[2]) == row["size"]:
        row["git_oid"] = oid


def _batch_blob_size(header, expected):
    """Accept exactly one canonical blob header for the requested full OID."""
    if not header.endswith(b"\n"):
        raise CandidateError("unterminated candidate object header")
    parts = header[:-1].split(b" ")
    if len(parts) != 3 or parts[0] != expected.encode("ascii") or parts[1] != b"blob":
        raise CandidateError("candidate object missing/type/OID mismatch")
    size = parts[2]
    if len(size) > _GIT_OBJECT_SIZE_MAX_DIGITS or re.fullmatch(rb"0|[1-9][0-9]*", size) is None:
        raise CandidateError("invalid candidate object size")
    return int(size)


def _resolve_native_lfs(pending, git, git_limit, object_format):
    """Batch only referenced immutable objects; keep no cross-capture cache."""
    grouped = {}
    oid_chars = hashlib.new(object_format).digest_size * 2
    for row, oid in pending:
        if re.fullmatch(r"[0-9a-f]{" + str(oid_chars) + r"}", oid) is None:
            raise CandidateError("invalid candidate index object OID")
        grouped.setdefault(oid, []).append(row)
    oids = sorted(grouped)
    # Header sizing bounds both metadata output and its smaller OID-only input.
    header_max = oid_chars + len(b" blob ") + _GIT_OBJECT_SIZE_MAX_DIGITS + len(b"\n")
    metadata_count = max(1, git_limit // header_max)

    def apply(oid, size, data):
        actual = hashlib.new(object_format, b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if len(data) != size or actual != oid:
            raise CandidateError("candidate object content/OID mismatch")
        for row in grouped[oid]:
            _apply_lfs_pointer(row, oid, data)

    def read_contents(batch):
        if not batch:
            return
        data = git(["cat-file", "--batch"], git_limit,
                   input_data=b"".join(oid.encode("ascii") + b"\n" for oid, _ in batch))
        position = 0
        for oid, size in batch:
            end = data.find(b"\n", position)
            if end < 0 or _batch_blob_size(data[position:end + 1], oid) != size:
                raise CandidateError("candidate object batch header mismatch")
            position = end + 1
            body = data[position:position + size]
            if len(body) != size or data[position + size:position + size + 1] != b"\n":
                raise CandidateError("incomplete candidate object batch")
            apply(oid, size, body)
            position += size + 1
        if position != len(data):
            raise CandidateError("unexpected candidate object batch output")

    for offset in range(0, len(oids), metadata_count):
        group = oids[offset:offset + metadata_count]
        metadata = git(["cat-file", "--batch-check"], git_limit,
                       input_data=b"".join(oid.encode("ascii") + b"\n" for oid in group))
        headers = metadata.splitlines(keepends=True)
        if len(headers) != len(group):
            raise CandidateError("incomplete candidate object metadata")
        sizes = [_batch_blob_size(header, oid) for header, oid in zip(headers, group)]
        contents, content_bytes = [], 0
        for oid, size in zip(group, sizes):
            if size > _LFS_POINTER_MAX_BYTES:
                continue  # A large indexed blob cannot be a supported pointer.
            response_bytes = len(oid) + len(b" blob ") + len(str(size)) + 1 + size + 1
            if response_bytes > git_limit:
                # Preserve the old fixed bare-pointer content limit when only
                # batch framing would exceed a small manifest/batch budget.
                read_contents(contents)
                contents, content_bytes = [], 0
                apply(oid, size, git(["cat-file", "blob", oid], _LFS_POINTER_MAX_BYTES))
            else:
                if content_bytes + response_bytes > git_limit:
                    read_contents(contents)
                    contents, content_bytes = [], 0
                contents.append((oid, size))
                content_bytes += response_bytes
        read_contents(contents)


def capture_candidate(workspace, *, base, code_files=None, scope_contract=None,
                      excluded_side_effects=None, inspection_scope=None, limits=None,
                      git_provider=None, require_git_provider=False):
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
    _candidate_provider(root, git_provider, require_git_provider,
                        deadline=deadline if git_provider is not None else None)

    def git(args, limit=git_limit, *, input_data=None):
        if git_provider is not None:
            raise CandidateError("raw Git forbidden for candidate provider")
        if time.monotonic() > deadline:
            raise CandidateError("candidate capture timeout")
        data = (_git(root, args, limit) if input_data is None
                else _git(root, args, limit, input_data=input_data))
        if time.monotonic() > deadline:
            raise CandidateError("candidate capture timeout")
        return data

    facts = None
    if git_provider is None:
        top = git(["rev-parse", "--show-toplevel"], 8192).decode().strip()
        if Path(top).resolve() != root:
            raise CandidateError("candidate workspace must be a Git checkout root")
        common_raw = git(["rev-parse", "--git-common-dir"], 8192).decode().strip()
        common = (root / common_raw).resolve()
        common_stat, checkout_stat = common.stat(), root.stat()
        head = git(["rev-parse", "--verify", "HEAD^{commit}"], 256).decode().strip()
    else:
        facts = _candidate_facts(root, git_provider, min(8192, git_limit), deadline=deadline)
        head = facts["head"]
    if not isinstance(base, str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base) is None:
        raise CandidateError("candidate base must be a resolved full commit oid")
    resolved_base = (git(["rev-parse", "--verify", base + "^{commit}"], 256).decode().strip()
                     if git_provider is None else facts["base"])
    if resolved_base != base:
        raise CandidateError("candidate base is not a commit")
    object_format = (git(["rev-parse", "--show-object-format"], 256).decode().strip()
                     if git_provider is None else facts["identity"]["object_format"])
    if object_format not in ("sha1", "sha256"):
        raise CandidateError("unsupported Git object format")
    exclusions = (_paths(excluded_side_effects or []) if git_provider is None else
                  _candidate_provider_paths([] if excluded_side_effects is None else excluded_side_effects))
    if "." in exclusions:
        raise CandidateError("excluding the entire candidate is prohibited")
    files = (_paths(code_files or []) if git_provider is None else
             _candidate_provider_paths([] if code_files is None else code_files))
    if "." in files:
        raise CandidateError("code_files must name files")
    if any(_excluded(x, [*CONTROL_DIRS, *exclusions]) for x in files):
        raise CandidateError("code_files overlap excluded/control artifacts")
    # Trees/index are plumbing manifests. No diff drivers, textconv or filters.
    if git_provider is None:
        head_raw = git(["ls-tree", "-rz", "--full-tree", "HEAD"])
        index_raw = git(["ls-files", "--stage", "-z"])
        others_raw = git(["ls-files", "--others", "--exclude-standard", "-z"])
    else:
        head_raw = _candidate_read(root, git_provider, CandidateRead.TREE, oid=head, limit=git_limit, deadline=deadline)
        index_raw = _candidate_read(root, git_provider, CandidateRead.INDEX, limit=git_limit, deadline=deadline)
        others_raw = _candidate_read(root, git_provider, CandidateRead.OTHERS, limit=git_limit, deadline=deadline)
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
    manifest, total_bytes, pending_lfs = [], 0, []
    for name in names:
        if git_provider is None:
            _paths([name])
        else:
            _candidate_provider_name(name)
        if _excluded(name, [*CONTROL_DIRS, *exclusions]):
            continue
        path = root / name
        if any(_path_is_candidate_link(p) for p in path.parents if p == root or root in p.parents):
            raise CandidateError(f"candidate symlink parent: {name}")
        if time.monotonic() > deadline:
            raise CandidateError("candidate capture timeout")
        try:
            before = path.lstat()
        except FileNotFoundError:
            continue  # Deleted paths are bound by absence in the final manifest.
        index = index_entries.get(name) or head_entries.get(name) or {}
        if index.get("mode") == "160000":
            if git_provider is not None:
                raise CandidateError("host-bound candidate gitlink unsupported")
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
        if _is_candidate_link(before):
            data = (_read_lx_symlink(path, before) if getattr(before, "st_reparse_tag", 0) == _IO_REPARSE_TAG_LX_SYMLINK
                    else os.fsencode(os.readlink(path)))
            if _stat_identity(before) != _stat_identity(path.lstat()):
                raise CandidateError(f"candidate symlink changed during capture: {name}")
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
            if git_provider is None:
                pending_lfs.append((row, oid))
            else:
                blob_size = int(_candidate_read(root, git_provider, CandidateRead.BLOB, oid=oid,
                    kind="size", limit=min(32, git_limit), deadline=deadline))
                pointer = (_candidate_read(root, git_provider, CandidateRead.BLOB, oid=oid,
                    kind="content", limit=min(4096, git_limit), deadline=deadline) if blob_size <= 4096 else b"")
                _apply_lfs_pointer(row, oid, pointer)
        manifest.append(row)
    if pending_lfs:
        _resolve_native_lfs(pending_lfs, git, git_limit, object_format)
    # Check Git identities again, so concurrent index/HEAD changes never yield a
    # mixed snapshot. Every file is race checked above; this is not a FS lock.
    if git_provider is None:
        if head != git(["rev-parse", "HEAD"], 256).decode().strip() or \
                index_raw != git(["ls-files", "--stage", "-z"]) or \
                others_raw != git(["ls-files", "--others", "--exclude-standard", "-z"]):
            raise CandidateError("candidate Git state changed during capture")
        identity = {"common_git_dir": str(common), "checkout_realpath": str(root),
                    "object_format": object_format,
                    "common_git_device": common_stat.st_dev, "common_git_inode": common_stat.st_ino,
                    "checkout_device": checkout_stat.st_dev, "checkout_inode": checkout_stat.st_ino}
    else:
        if time.monotonic() > deadline:
            raise CandidateError("candidate capture timeout")
        if facts != _candidate_facts(root, git_provider, min(8192, git_limit), deadline=deadline) or \
                index_raw != _candidate_read(root, git_provider, CandidateRead.INDEX, limit=git_limit, deadline=deadline) or \
                others_raw != _candidate_read(root, git_provider, CandidateRead.OTHERS, limit=git_limit, deadline=deadline):
            raise CandidateError("candidate provider state changed during capture")
        identity = dict(facts["identity"])
    content = {"identity": identity, "base": base, "code_files": files,
               "scope_contract": scope_contract, "inspection_scope": inspection_scope or {},
               "excluded_side_effects": exclusions, "control_artifact_exclusions": list(CONTROL_DIRS),
               "manifest": manifest}
    snapshot = {"version": 1, **content, "head": head,
                "head_tree": (git(["rev-parse", "HEAD^{tree}"], 256).decode().strip()
                              if git_provider is None else facts["head_tree"]),
                "index_manifest": [{"path": k, **v} for k, v in sorted(index_entries.items())
                                   if not _excluded(k, [*CONTROL_DIRS, *exclusions])],
                "untracked_manifest": [row for row in manifest if row["path"] in untracked],
                "content_id": _digest(content)}
    snapshot["candidate_id"] = _digest(snapshot)
    if git_provider is not None:
        _candidate_provider(root, git_provider, True, deadline=deadline)
    if time.monotonic() > deadline:
        raise CandidateError("candidate capture timeout")
    return snapshot


def compare_candidates(recorded, current, *, git_provider=None, require_git_provider=False):
    """Exact equality or strict linear same-final-content commit proof."""
    deadline = (time.monotonic() + DEFAULT_LIMITS["timeout_seconds"] if git_provider is not None else None)
    if not snapshot_identity_ok(recorded) or not snapshot_identity_ok(current):
        return {"equivalent": False, "reason": "candidate_invalid"}
    try:
        _candidate_provider(Path(current["identity"]["checkout_realpath"]), git_provider,
                            require_git_provider, deadline=deadline)
    except CandidateError:
        return {"equivalent": False, "reason": "commit_equivalence_unproven"}
    if recorded.get("candidate_id") == current.get("candidate_id"):
        if git_provider is not None:
            try:
                _candidate_provider(Path(current["identity"]["checkout_realpath"]), git_provider,
                                    True, deadline=deadline)
            except CandidateError:
                return {"equivalent": False, "reason": "commit_equivalence_unproven"}
        return {"equivalent": True, "reason": "identical_candidate"}
    if recorded.get("content_id") != current.get("content_id"):
        return {"equivalent": False, "reason": "source_or_contract_drift"}
    if recorded.get("head") == current.get("head"):
        return {"equivalent": False, "reason": "index_or_manifest_drift"}
    root = Path(current["identity"]["checkout_realpath"])
    old_head, new_head = recorded["head"], current["head"]
    try:
        if git_provider is None:
            ancestors = _git(root, ["rev-list", "--parents", "--ancestry-path", f"{old_head}..{new_head}"],
                             DEFAULT_LIMITS["max_git_bytes"]).decode().splitlines()
        else:
            ancestors = _candidate_read(root, git_provider, CandidateRead.LINEAR,
                oid=new_head, old_oid=old_head, limit=DEFAULT_LIMITS["max_git_bytes"],
                deadline=deadline).decode("ascii").splitlines()
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
        if git_provider is None:
            deadline = time.monotonic() + DEFAULT_LIMITS["timeout_seconds"]
        for line in ancestors:
            if time.monotonic() > deadline:
                raise CandidateError("commit equivalence timeout")
            if git_provider is None:
                tree = _git(root, ["ls-tree", "-rz", "--full-tree", line.split()[0]], DEFAULT_LIMITS["max_git_bytes"])
            else:
                tree = _candidate_read(root, git_provider, CandidateRead.TREE,
                    oid=line.split()[0], limit=DEFAULT_LIMITS["max_git_bytes"], deadline=deadline)
            if _tree_entries(tree, exclusions) != reviewed:
                return {"equivalent": False, "reason": "commit_tree_not_reviewed_content"}
    except (CandidateError, KeyError, UnicodeError):
        return {"equivalent": False, "reason": "commit_equivalence_unproven"}
    if git_provider is not None:
        try:
            _candidate_provider(root, git_provider, True, deadline=deadline)
        except CandidateError:
            return {"equivalent": False, "reason": "commit_equivalence_unproven"}
    return {"equivalent": True, "reason": "same_content_commit", "from_head": old_head, "to_head": new_head}


def review_source_equivalence(recorded, current, *, git_provider=None, require_git_provider=False):
    """A review may establish its declaration, but may not silently change source."""
    if not snapshot_identity_ok(recorded) or not snapshot_identity_ok(current):
        return {"equivalent": False, "reason": "review_candidate_missing"}
    normalized = dict(current, inspection_scope=recorded["inspection_scope"])
    keys = ("identity", "base", "code_files", "scope_contract", "inspection_scope",
            "excluded_side_effects", "control_artifact_exclusions", "manifest")
    normalized["content_id"] = _digest({k: normalized[k] for k in keys})
    normalized["candidate_id"] = _digest({k: v for k, v in normalized.items() if k != "candidate_id"})
    return compare_candidates(recorded, normalized, git_provider=git_provider,
                              require_git_provider=require_git_provider)


def delivery_freshness(meta, current, events=None, required_steps=REVIEW_STEPS, *,
                       git_provider=None, require_git_provider=False):
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
    if git_provider is not None or type(require_git_provider) is not bool or require_git_provider:
        try:
            _candidate_provider(Path(current["identity"]["checkout_realpath"]), git_provider, require_git_provider)
        except (CandidateError, KeyError, TypeError):
            return {"ok": False, "state": "blocked", "reasons": ["candidate_provider_context"],
                    "candidate_id": current.get("candidate_id"), "applicable_receipts": [],
                    "required_steps": list(required_steps)}
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
        proof = compare_candidates(receipt.get("candidate"), current, git_provider=git_provider,
                                   require_git_provider=require_git_provider)
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
