"""Install pinned MCP packages once; return their real JavaScript CLI paths.

All public metadata paths are absolute strings. npm exists only during installation;
callers launch ``metadata['node'], metadata['entry']`` directly. Existing runtimes
are immutable and are never repaired, overwritten or garbage-collected here.
"""
import argparse
import base64
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit


CATALOG_ROOT = Path(__file__).resolve().parents[1] / "node-packages"
MARKER = ".icode-managed-node.json"
RECEIPT = ".icode-node-receipt.json"
OWNER = "icode-managed-node-v1"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z")
PACKAGE = re.compile(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+\Z")


def _json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("invalid managed Node metadata") from exc
    if not isinstance(value, dict):
        raise ValueError("managed Node metadata must be an object")
    return value


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(value):
    if (not isinstance(value, str) or not value or "\\" in value
            or ":" in value or value.startswith("/")
            or any(p in ("", ".", "..") for p in value.split("/"))):
        raise ValueError("unsafe managed Node relative path")
    return PurePosixPath(value)


def _no_links(path):
    """Reject symlinks and Windows junctions before resolving a supplied path."""
    path = Path(path).absolute()
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("managed Node paths may not contain links or junctions")
    return path


def _package_path(value):
    parts = _relative(value).parts
    index = 0
    while index < len(parts):
        if parts[index] != "node_modules" or index + 1 >= len(parts):
            raise ValueError("invalid package location in Node lock")
        index += 1
        name = parts[index]
        index += 1
        if name.startswith("@"):
            if index >= len(parts):
                raise ValueError("incomplete scoped package in Node lock")
            name += "/" + parts[index]
            index += 1
        if not PACKAGE.fullmatch(name):
            raise ValueError("invalid package name in Node lock")
    return value


def _dependency_location(packages, location, name):
    # npm looks in the package's own node_modules, then each enclosing scope.
    current = location
    while True:
        candidate = (current + "/" if current else "") + "node_modules/" + name
        if candidate in packages:
            return candidate
        if not current:
            return None
        if "/node_modules/" in current:
            current = current.rsplit("/node_modules/", 1)[0]
        else:
            current = ""


def _validate_lock(manifest, lock, package, version):
    if (lock.get("lockfileVersion") != 3 or not isinstance(lock.get("packages"), dict)
            or manifest.get("private") is not True
            or manifest.get("dependencies") != {package: version}
            or set(manifest) - {"name", "version", "private", "dependencies"}):
        raise ValueError("managed Node manifests require one exact pinned dependency")
    packages = lock["packages"]
    root = packages.get("")
    if (not isinstance(root, dict) or root.get("dependencies") != manifest["dependencies"]
            or lock.get("name") != manifest.get("name")
            or lock.get("version") != manifest.get("version")):
        raise ValueError("managed Node lock does not match its manifest")
    for location, item in packages.items():
        if not isinstance(item, dict):
            raise ValueError("invalid managed Node lock package")
        if location:
            _package_path(location)
            if item.get("link") or not VERSION.fullmatch(str(item.get("version", ""))):
                raise ValueError("managed Node lock contains an unpinned package or link")
            try:
                resolved, integrity = item.get("resolved"), item.get("integrity")
                if not isinstance(resolved, str) or not isinstance(integrity, str):
                    raise ValueError("invalid registry metadata types")
                url = urlsplit(resolved)
                public = (url.scheme == "https" and url.hostname == "registry.npmjs.org"
                          and not url.username and not url.password and url.port is None
                          and not url.query and not url.fragment and bool(url.path)
                          and all(p not in (".", "..") for p in unquote(url.path).split("/")))
                valid_integrity = (integrity.startswith("sha512-") and
                                   len(base64.b64decode(integrity[7:], validate=True)) == 64)
            except (ValueError, TypeError):
                public = valid_integrity = False
            if not public or not valid_integrity:
                raise ValueError("managed Node lock requires public registry URLs and SHA512 integrity")
        peer_meta = item.get("peerDependenciesMeta", {})
        if not isinstance(peer_meta, dict) or any(not isinstance(v, dict) for v in peer_meta.values()):
            raise ValueError("invalid managed Node peer dependency metadata")
        for field in ("dependencies", "optionalDependencies", "peerDependencies"):
            deps = item.get(field, {})
            if not isinstance(deps, dict):
                raise ValueError("invalid managed Node dependency map")
            for name, requested in deps.items():
                if (not PACKAGE.fullmatch(name) or not isinstance(requested, str)
                        or not re.fullmatch(r"[0-9A-Za-z.*+^~<>=|() -]+", requested)):
                    raise ValueError("unsafe managed Node dependency specifier")
                optional_peer = (field == "peerDependencies" and
                                 peer_meta.get(name, {}).get("optional"))
                if not optional_peer and _dependency_location(packages, location, name) is None:
                    raise ValueError("managed Node lock is missing a dependency")
    if packages.get("node_modules/" + package, {}).get("version") != version:
        raise ValueError("managed Node lock target version mismatch")


def load_spec(server, catalog_root=None):
    """Return validated catalog metadata with absolute catalog ``root`` string."""
    catalog_root = _no_links(catalog_root or CATALOG_ROOT).resolve()
    _no_links(catalog_root / "catalog.json")
    catalog = _json(catalog_root / "catalog.json")
    if catalog.get("schema_version") != 1 or not re.fullmatch(r"[a-z0-9-]+", server):
        raise ValueError("invalid managed Node catalog or server")
    record = catalog.get("servers", {}).get(server)
    if not isinstance(record, dict):
        raise ValueError("unknown managed Node server")
    package, version, entry = (record.get(key) for key in ("package", "version", "bin"))
    if (not isinstance(package, str) or not PACKAGE.fullmatch(package)
            or not isinstance(version, str) or not VERSION.fullmatch(version)):
        raise ValueError("managed Node catalog requires exact package versions")
    _relative(entry)
    if not entry.endswith(".js"):
        raise ValueError("managed Node entry must be JavaScript")
    root = _no_links(catalog_root / server)
    for filename in ("package.json", "package-lock.json"):
        _no_links(root / filename)
    manifest, lock = _json(root / "package.json"), _json(root / "package-lock.json")
    _validate_lock(manifest, lock, package, version)
    return {"server": server, "package": package, "version": version, "bin": entry,
            "root": str(root), "lock_sha256": _sha(root / "package-lock.json")}


def default_runtime_root():
    """Private user state, separate from every AI client's skill discovery root."""
    if os.name == "nt":
        state = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    else:
        state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return state / "icode/mcp-node"


def resolve_npm_cli(node_path):
    """Find npm bundled beside this Node, without PATH, npx or shell wrappers."""
    node = Path(node_path).expanduser().resolve()
    if not node.is_file():
        raise ValueError("Node executable does not exist")
    for candidate in (node.parent / "node_modules/npm/bin/npm-cli.js",
                      node.parent.parent / "lib/node_modules/npm/bin/npm-cli.js",
                      node.parent.parent / "share/nodejs/npm/bin/npm-cli.js"):
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError("npm-cli.js is missing beside the selected Node executable")


def _files(root):
    result = {}
    def fail_walk(error):
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail_walk):
        for name in dirs + files:
            path = Path(directory) / name
            info = path.lstat()
            # Each parent was already checked by this top-down walk; avoid a
            # repeated ancestor stat chain for every file (costly on SMB).
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("managed Node paths may not contain links or junctions")
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("managed Node runtime requires ordinary unlinked files")
            relative = path.relative_to(root).as_posix()
            if relative != RECEIPT:
                result[relative] = _sha(path)
    return result


def _installed(root, spec):
    if _sha(root / "package-lock.json") != spec["lock_sha256"]:
        raise ValueError("managed Node runtime lock changed")
    manifest = _json(root / "package.json")
    lock = _json(root / "package-lock.json")
    _validate_lock(manifest, lock, spec["package"], spec["version"])
    for location, record in lock["packages"].items():
        if not location:
            continue
        package_dir = root / location
        if not package_dir.exists() and (record.get("optional") or record.get("dev")):
            continue
        meta = _json(package_dir / "package.json")
        name = location.rsplit("node_modules/", 1)[-1]
        if meta.get("version") != record["version"] or meta.get("name") != name:
            raise ValueError("installed Node dependency name or version mismatch")
    target = root / "node_modules" / spec["package"]
    meta = _json(target / "package.json")
    bins = meta.get("bin", {})
    bins = [bins] if isinstance(bins, str) else list(bins.values()) if isinstance(bins, dict) else []
    entry = target / spec["bin"]
    if (meta.get("name") != spec["package"] or spec["bin"] not in bins
            or not entry.is_file() or not entry.resolve().is_relative_to(target.resolve())):
        raise ValueError("installed Node package bin mismatch")
    return entry


def verify_runtime(server, runtime_path, *, catalog_root=None):
    """Verify the package tree; ``node`` records its original install executable.

    Node may subsequently be upgraded or removed. ``ensure_runtime`` validates
    the requested executable separately and returns that current Node path.
    """
    spec = load_spec(server, catalog_root)
    return _verify_runtime_spec(server, runtime_path, spec)


def verify_owned_runtime(server, runtime_path, *, package, version, lock_sha256, entry):
    """Verify a previous runtime against its durable host-receipt identity.

    ``entry`` must come from the digest-bound actual host launch. The package,
    version and lock digest come from that host's saved registration receipt,
    never from the current catalog or unverified old runtime metadata. This lets
    an upgrade validate its old launch before provisioning the new catalog lock.
    No package code is executed and the original installation Node may be gone.
    """
    if (not all(isinstance(value, str) for value in (server, package, version, lock_sha256, entry))
            or not re.fullmatch(r"[a-z0-9-]+", server) or not PACKAGE.fullmatch(package)
            or any(part in (".", "..") for part in package.split("/"))
            or not VERSION.fullmatch(version) or not re.fullmatch(r"[0-9a-f]{64}", lock_sha256)):
        raise ValueError("invalid previous managed Node runtime identity")
    root = _no_links(runtime_path).resolve()
    expected_entry = Path(entry)
    if not expected_entry.is_absolute():
        raise ValueError("previous managed Node entry must be absolute")
    try:
        relative = expected_entry.relative_to(root / "node_modules" / package).as_posix()
    except ValueError as exc:
        raise ValueError("previous managed Node entry is outside its package") from exc
    _relative(relative)
    if not relative.endswith(".js"):
        raise ValueError("previous managed Node entry must be JavaScript")
    spec = {"package": package, "version": version, "lock_sha256": lock_sha256, "bin": relative}
    result = _verify_runtime_spec(server, root, spec)
    if result["entry"] != str(expected_entry):
        raise ValueError("previous managed Node launch entry mismatch")
    return result


def _verify_runtime_spec(server, runtime_path, spec):
    """Shared fail-closed validation for current or receipt-bound old specs."""
    root = _no_links(runtime_path).resolve()
    if not root.is_dir():
        raise ValueError("managed Node runtime directory is missing")
    if os.name != "nt" and root.stat().st_uid != os.getuid():
        raise ValueError("managed Node runtime belongs to another user")
    # Establish ownership before walking any tree: an unrelated directory may
    # contain user graphs or credentials and is not an installation to inspect.
    for filename in (MARKER, RECEIPT):
        path = _no_links(root / filename)
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise ValueError("managed Node runtime ownership metadata is missing") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("managed Node runtime metadata must be ordinary files")
    marker = _json(root / MARKER)
    expected = {"owner": OWNER, "server": server, "lock_sha256": spec["lock_sha256"]}
    if marker != expected:
        raise ValueError("managed Node runtime ownership marker mismatch")
    files = _files(root)
    receipt = _json(root / RECEIPT)
    if (receipt.get("schema_version") != 1 or receipt.get("files") != files
            or not isinstance(receipt.get("node"), str)
            or not Path(receipt["node"]).is_absolute()):
        raise ValueError("managed Node runtime receipt mismatch")
    entry = _installed(root, spec)
    return {"root": str(root), "entry": str(entry), "package": spec["package"],
            "version": spec["version"], "lock_sha256": spec["lock_sha256"], "node": receipt["node"]}


def _publish(source, destination):
    """Atomic no-replace rename, including when an existing target is empty."""
    if os.name == "nt":
        # MoveFile semantics fail if any destination directory already exists.
        source.rename(destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(source), os.fsencode(destination), 4)
    else:
        raise RuntimeError("atomic no-replace Node package publication is unavailable")
    if result:
        code = ctypes.get_errno()
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise FileExistsError(code, "managed Node runtime already exists")
        raise OSError(code, "atomic managed Node runtime publication failed")


def ensure_runtime(server, node_path, *, runtime_root=None, catalog_root=None):
    """Install to a private sibling, validate fully, then publish by atomic rename."""
    spec = load_spec(server, catalog_root)
    node = Path(node_path).expanduser().resolve()
    if not node.is_file():
        raise ValueError("Node executable does not exist")
    base = _no_links(runtime_root or default_runtime_root()).resolve()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = base / (server + "-" + spec["lock_sha256"])
    if destination.exists() or destination.is_symlink():
        result = verify_runtime(server, destination, catalog_root=catalog_root)
        result["node"] = str(node)
        return result
    npm = resolve_npm_cli(node)
    temporary = Path(tempfile.mkdtemp(prefix=".install-" + server + "-", dir=base))
    os.chmod(temporary, 0o700)
    # Keep failed staging trees for diagnosis. Never delete or repair user trees.
    for filename in ("package.json", "package-lock.json"):
        (temporary / filename).write_bytes((Path(spec["root"]) / filename).read_bytes())
    command = [str(node), str(npm), "ci", "--ignore-scripts", "--omit=dev",
               "--bin-links=false", "--no-audit", "--no-fund"]
    try:
        result = subprocess.run(command, cwd=str(temporary), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, shell=False, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("managed Node package installation could not complete") from exc
    if result.returncode:
        raise RuntimeError("managed Node package installation failed (exit %d)" % result.returncode)
    _write_json(temporary / MARKER, {"owner": OWNER, "server": server,
                                   "lock_sha256": spec["lock_sha256"]})
    _write_json(temporary / RECEIPT, {"schema_version": 1, "node": str(node),
                                    "files": _files(temporary)})
    result = verify_runtime(server, temporary, catalog_root=catalog_root)
    try:
        _publish(temporary, destination)
    except FileExistsError:
        # Another installer may have published first; accept only a valid winner.
        if not destination.exists():
            raise
        result = verify_runtime(server, destination, catalog_root=catalog_root)
    else:
        # Atomic rename preserves the fully verified tree, so reuse its result.
        result["root"] = str(destination)
        result["entry"] = str(destination / Path(result["entry"]).relative_to(temporary))
    result["node"] = str(node)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    ensure = commands.add_parser("ensure")
    ensure.add_argument("--server", required=True)
    ensure.add_argument("--node", required=True)
    ensure.add_argument("--runtime-root")
    ensure.add_argument("--catalog-root")
    args = parser.parse_args(argv)
    try:
        result = ensure_runtime(args.server, args.node, runtime_root=args.runtime_root,
                                catalog_root=args.catalog_root)
    except (ValueError, OSError, RuntimeError):
        # Do not persist npm output, URLs, environment values or credential errors.
        sys.stderr.write("managed Node package verification or installation failed\n")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
