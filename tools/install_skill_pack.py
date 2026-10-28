#!/usr/bin/env python3
"""Safely publish ICODE bundled skills into one or more host skill roots."""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.dont_write_bytecode = True

from validate_skill_pack import (
    OWNER_MARKER,
    RUNTIME_FILES,
    RUNTIME_NAMES,
    ValidationError,
    compare_source_files,
    installed_digest,
    iter_source_publish_files,
    validate_skills,
)


OWNER = "icode-skill"
MAX_COMPONENT_BYTES = 64 * 1024 * 1024
MAX_COMPONENT_FILES = 512
MAX_COMPONENT_MANIFEST_BYTES = 4 * 1024 * 1024
COMPONENT_DOWNLOAD_SECONDS = 60
MAX_CORE_RUNTIME_BYTES = 5 * 1024 * 1024
DOWNLOAD_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


class InstallConflict(RuntimeError):
    """Raised when an existing target is not owned by this installer."""


def component_no_links(path):
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and getattr(item.lstat(), "st_file_attributes", 0) & 0x400):
            raise InstallConflict(f"component path must not contain links: {item}")


def component_path(name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise InstallConflict("unsafe component path")
    path = PurePosixPath(name)
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if (not path.parts or path.is_absolute() or path.as_posix() != name or any(
            part in {".", ".."} or part.rstrip(" .") != part
            or part.split(".")[0].upper() in reserved for part in path.parts)):
        raise InstallConflict("unsafe component path")
    return Path(*path.parts)


def component_hash(path, expected_size=None):
    component_no_links(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_COMPONENT_BYTES:
        raise InstallConflict("component input is not a bounded regular file")
    if expected_size is not None and info.st_size != expected_size:
        raise InstallConflict("component file size mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_component_url(url):
    if not isinstance(url, str):
        raise InstallConflict("component download URL is missing")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in DOWNLOAD_HOSTS
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise InstallConflict("component downloads require an HTTPS GitHub release host")
    return url


class ComponentRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        check_component_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def obtain_component(spec, cache=None):
    """Fetch a public, pinned archive; never run it or accept credentials."""
    url = check_component_url(spec.get("url", ""))
    cache = cache or Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "icode/components"
    cache = Path(cache).expanduser().absolute()
    component_no_links(cache)
    target = cache / (spec["sha256"] + ".zip")
    component_no_links(target)
    if target.exists():
        if not stat.S_ISREG(target.stat().st_mode):
            raise InstallConflict("component cache entry must be a regular file")
        if target.stat().st_size == spec["size"] and component_hash(target, spec["size"]) == spec["sha256"]:
            return target
    cache.mkdir(parents=True, exist_ok=True)
    # Temporary download names are private to this call. Publish only after hash
    # validation, so an interrupted transfer cannot become a usable cache entry.
    with tempfile.TemporaryDirectory(prefix="component-", dir=cache) as temporary:
        pending = Path(temporary) / "download.zip"
        request = Request(url, headers={"User-Agent": "ICODE-component-installer", "Accept": "application/octet-stream"})
        deadline = time.monotonic() + COMPONENT_DOWNLOAD_SECONDS
        with build_opener(ComponentRedirects()).open(request, timeout=30) as response, pending.open("xb") as stream:
            check_component_url(response.geturl())
            count = 0
            while True:
                if time.monotonic() > deadline:
                    raise InstallConflict("component download deadline exceeded")
                block = response.read(1024 * 1024)
                if not block:
                    break
                count += len(block)
                if count > spec["size"]:
                    raise InstallConflict("component download exceeds declared size")
                stream.write(block)
        if component_hash(pending, spec["size"]) != spec["sha256"]:
            raise InstallConflict("component archive hash mismatch")
        os.replace(pending, target)
    return target


def install_component_files(root, component, archive=None, dry_run=False, cache=None):
    root = Path(root).expanduser().absolute()
    if ".." in root.parts or root == Path(root.anchor) or root == Path.home().absolute():
        raise InstallConflict("component root must be a specific skill directory")
    component_no_links(root)
    manifest_path = root / "components.json"
    component_no_links(manifest_path)
    if manifest_path.stat().st_size > MAX_COMPONENT_MANIFEST_BYTES:
        raise InstallConflict("component manifest exceeds size limit")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = root / "SKILL.md"
    component_no_links(entry)
    if entry.stat().st_size > MAX_COMPONENT_MANIFEST_BYTES:
        raise InstallConflict("component entry exceeds size limit")
    version = re.search(r"^\*\*版本\*\*:\s*v(\d+\.\d+\.\d+)\s*$", entry.read_text(encoding="utf-8"), re.M)
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("profile") != "core" or not version
            or manifest.get("version") != version.group(1)):
        raise InstallConflict("component version/profile mismatch")
    components = manifest.get("components")
    if not isinstance(components, dict) or component not in components:
        raise InstallConflict("unknown component")
    spec = components[component]
    if (not isinstance(spec, dict) or not isinstance(spec.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", spec["sha256"])
            or type(spec.get("size")) is not int or not 0 < spec["size"] <= MAX_COMPONENT_BYTES
            or not isinstance(spec.get("files"), list) or len(spec["files"]) > MAX_COMPONENT_FILES):
        raise InstallConflict("invalid component archive declaration")
    expected = {}
    missing = []
    for row in spec["files"]:
        if not isinstance(row, dict):
            raise InstallConflict("invalid component file declaration")
        relative = component_path(row.get("path"))
        if (row["path"] in expected or type(row.get("size")) is not int or row["size"] < 0
                or not isinstance(row.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                or type(row.get("mode")) is not int or row["mode"] not in {0o644, 0o755}):
            raise InstallConflict("invalid/duplicate component file declaration")
        target = root / relative
        component_no_links(target)
        for parent in target.parents:
            if parent == root.parent:
                break
            if parent.exists() and not parent.is_dir():
                raise InstallConflict("component parent conflicts with an existing file")
        if target.exists():
            if component_hash(target, row["size"]) != row["sha256"]:
                raise InstallConflict(f"component conflicts with existing content: {row['path']}")
        else:
            missing.append(row["path"])
        expected[row["path"]] = row
    folded = {name.casefold() for name in expected}
    if len(folded) != len(expected) or any(
            parent.as_posix().casefold() in folded
            for name in expected for parent in PurePosixPath(name).parents if parent != PurePosixPath(".")):
        raise InstallConflict("component paths collide or contain a file/directory conflict")
    if sum(row["size"] for row in expected.values()) > MAX_COMPONENT_BYTES:
        raise InstallConflict("component expanded size limit exceeded")
    report = {"ok": True, "component": component, "version": version.group(1), "dry_run": dry_run,
              "files": len(expected), "new_files": len(missing), "download_required": bool(missing and archive is None)}
    if archive is None and (dry_run or not missing):
        return report
    archive = Path(archive).expanduser().absolute() if archive is not None else obtain_component(spec, cache)
    if component_hash(archive, spec["size"]) != spec["sha256"]:
        raise InstallConflict("component archive hash mismatch")
    with zipfile.ZipFile(archive) as zipped:
        infos = zipped.infolist()
        if len(infos) != len(expected) or len({i.filename for i in infos}) != len(infos):
            raise InstallConflict("component archive entries differ from declaration")
        # Validate every archive member before creating any destination file.
        for info in infos:
            component_path(info.filename)
            mode = info.external_attr >> 16
            if (info.filename not in expected or info.is_dir() or info.flag_bits & 1
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                    or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or info.file_size != expected[info.filename]["size"]):
                raise InstallConflict("unsafe or undeclared component archive entry")
            if hashlib.sha256(zipped.read(info)).hexdigest() != expected[info.filename]["sha256"]:
                raise InstallConflict("component file hash mismatch")
        if not dry_run:
            for name in missing:
                target = root / component_path(name)
                component_no_links(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                component_no_links(target)
                with target.open("xb") as stream:
                    stream.write(zipped.read(name))
                target.chmod(expected[name]["mode"])
    report["download_required"] = False
    return report


def prepare_component_runtime(source, cache, dry_run=False):
    """Keep expanded resources outside the Agent's bounded discovery directory."""
    source = Path(source).expanduser().absolute()
    if ".." in source.parts or source == Path(source.anchor) or source == Path.home().absolute():
        raise InstallConflict("component root must be a specific skill directory")
    component_no_links(source)
    path = source / "components.json"
    component_no_links(path)
    if path.stat().st_size > MAX_COMPONENT_MANIFEST_BYTES:
        raise InstallConflict("component manifest exceeds size limit")
    raw = path.read_bytes()
    manifest = json.loads(raw.decode("utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("core_files"), list):
        raise InstallConflict("component manifest requires a pinned core inventory")
    rows = manifest["core_files"]
    if len(rows) > 200:
        raise InstallConflict("core runtime file limit exceeded")
    payload = {}
    modes = {}
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            raise InstallConflict("invalid core runtime declaration")
        relative = component_path(row.get("path"))
        if (relative.as_posix() == "components.json" or relative.as_posix() in payload
                or type(row.get("size")) is not int or not 0 <= row["size"] <= MAX_CORE_RUNTIME_BYTES
                or type(row.get("mode")) is not int or row["mode"] not in {0o644, 0o755}
                or not isinstance(row.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])):
            raise InstallConflict("invalid core runtime declaration")
        total += row["size"]
        if total > MAX_CORE_RUNTIME_BYTES:
            raise InstallConflict("core runtime byte limit exceeded")
        origin = source / relative
        if component_hash(origin, row["size"]) != row["sha256"]:
            raise InstallConflict("core source differs from its pinned inventory")
        data = origin.read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise InstallConflict("core source changed while preparing runtime")
        payload[relative.as_posix()] = data
        modes[relative.as_posix()] = row["mode"]
    if not {"SKILL.md", "WORKFLOW.md", "tools/install_skill_pack.py"} <= set(payload):
        raise InstallConflict("core runtime is missing its required entrypoints")
    payload["components.json"] = raw
    modes["components.json"] = 0o644
    runtime = cache / "runtimes" / hashlib.sha256(raw).hexdigest() / "icode"
    component_no_links(runtime)
    if runtime.exists():
        for name, data in payload.items():
            target = runtime / component_path(name)
            if component_hash(target, len(data)) != hashlib.sha256(data).hexdigest():
                raise InstallConflict("cached runtime core has different content")
        return runtime
    if dry_run:
        return None
    runtime.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="core-", dir=runtime.parent) as temporary:
        stage = Path(temporary) / "icode"
        stage.mkdir()
        for name, data in payload.items():
            target = stage / component_path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(modes[name])
        stage.rename(runtime)
    return runtime


def install_component(root, component, archive=None, dry_run=False, cache=None):
    root = Path(root).expanduser().absolute()
    cache = Path(cache or Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "icode/components").expanduser().absolute()
    if ".." in cache.parts or cache == Path(cache.anchor) or cache == Path.home().absolute():
        raise InstallConflict("component cache must be a specific directory")
    component_no_links(cache)
    discovery = root
    if root.parent.name.casefold() == "skills":
        package = root.parent.parent
        if any((package / f".{host}-plugin/plugin.json").is_file() for host in ("claude", "codex", "codebuddy")):
            discovery = package
    if cache.is_relative_to(discovery) or discovery.is_relative_to(cache):
        raise InstallConflict("component cache must be outside the Agent discovery package")
    # Validate declarations/version and explicit archives against the untouched
    # source first. A bad archive cannot create a runtime or append skill files.
    install_component_files(root, component, archive, dry_run=True, cache=cache / "archives")
    runtime = prepare_component_runtime(root, cache, dry_run=True)
    if not dry_run and runtime is None:
        if archive is None:
            manifest = json.loads((root / "components.json").read_text(encoding="utf-8"))
            archive = obtain_component(manifest["components"][component], cache / "archives")
            install_component_files(root, component, archive, dry_run=True, cache=cache / "archives")
        runtime = prepare_component_runtime(root, cache)
    result = install_component_files(runtime or root, component, archive, dry_run, cache / "archives")
    result["runtime_root"] = str(runtime) if runtime is not None else None
    return result


def expected_marker(name: str) -> dict:
    return {"schema_version": 1, "owner": OWNER, "skill": name}


def read_marker(target: Path) -> dict | None:
    marker_path = target / OWNER_MARKER
    if not marker_path.exists():
        return None
    try:
        value = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallConflict(f"所有权标记损坏: {marker_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InstallConflict(f"所有权标记必须是 JSON 对象: {marker_path}")
    return value


def validate_target_root(raw: str) -> Path:
    root = Path(raw).expanduser().resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise InstallConflict(f"拒绝使用过宽的技能根目录: {root}")
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise InstallConflict(f"技能根目录不是普通目录: {root}")
    return root


def classify_target(row: dict, target: Path, keep_extra: bool) -> str:
    if not target.exists():
        return "create"
    if not target.is_dir() or target.is_symlink():
        raise InstallConflict(f"目标技能不是普通目录: {target}")
    marker = read_marker(target)
    if marker is not None:
        if marker != expected_marker(row["name"]):
            raise InstallConflict(f"目标技能所有权冲突: {target}")
        if keep_extra:
            try:
                compare_source_files(
                    Path(row["path"]), row["entrypoint"], target)
            except ValidationError:
                return "update"
            return "current"
        if installed_digest(target) == row["digest"]:
            return "current"
        return "update"
    if installed_digest(target) == row["digest"]:
        return "adopt"
    raise InstallConflict(
        f"存在未托管的同名技能: {target}; 请先改名或移走后重试")


def is_runtime_path(rel: Path) -> bool:
    return (
        any(part in RUNTIME_NAMES for part in rel.parts)
        or rel.name in RUNTIME_FILES
        or rel.suffix in {".pyc", ".pyo"}
    )


def preserve_runtime_files(target: Path, stage: Path) -> None:
    if not target.is_dir():
        return
    for source in sorted(target.rglob("*")):
        rel = source.relative_to(target)
        if not is_runtime_path(rel):
            continue
        destination = stage / rel
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def prepare_stage(row: dict, root: Path, target: Path, keep_extra: bool) -> Path:
    stage = Path(tempfile.mkdtemp(
        prefix=f".{row['name']}.icode-stage-", dir=root))
    try:
        source_root = Path(row["path"])
        if keep_extra and target.is_dir():
            shutil.copytree(target, stage, dirs_exist_ok=True)
        for source, rel in iter_source_publish_files(
                source_root, row["entrypoint"]):
            destination = stage / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        if not keep_extra:
            preserve_runtime_files(target, stage)
        marker_path = stage / OWNER_MARKER
        marker_path.write_text(
            json.dumps(expected_marker(row["name"]), ensure_ascii=False,
                       sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if keep_extra:
            compare_source_files(source_root, row["entrypoint"], stage)
        else:
            actual = installed_digest(stage)
            if actual != row["digest"]:
                raise ValidationError(
                    f"暂存技能 hash 不一致: {target} "
                    f"expected={row['digest']} actual={actual}")
        return stage
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def remove_generated_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def commit_stages(actions: list[dict]) -> None:
    committed = []
    try:
        for action in actions:
            target = action["target"]
            stage = action["stage"]
            backup = target.parent / (
                f".{target.name}.icode-backup-{uuid.uuid4().hex}")
            if target.exists():
                target.rename(backup)
            else:
                backup = None
            try:
                stage.rename(target)
            except Exception:
                if backup is not None and backup.exists():
                    backup.rename(target)
                raise
            committed.append({"target": target, "backup": backup})
    except Exception:
        for item in reversed(committed):
            remove_generated_tree(item["target"])
            backup = item["backup"]
            if backup is not None and backup.exists():
                backup.rename(item["target"])
        raise
    else:
        for item in committed:
            backup = item["backup"]
            if backup is not None:
                remove_generated_tree(backup)
    finally:
        for action in actions:
            remove_generated_tree(action["stage"])


def install(manifest: Path, raw_roots: list[str], dry_run: bool,
            keep_extra: bool) -> list[dict]:
    rows = validate_skills(manifest)
    roots = []
    for raw in raw_roots:
        root = validate_target_root(raw)
        if root in roots:
            raise InstallConflict(f"目标技能根目录重复: {root}")
        roots.append(root)

    actions = []
    for root in roots:
        for row in rows:
            target = root / row["name"]
            actions.append({
                "row": row,
                "root": root,
                "target": target,
                "action": classify_target(row, target, keep_extra),
            })

    if dry_run:
        return actions

    try:
        pending = [action for action in actions
                   if action["action"] != "current"]
        for root in {action["root"] for action in pending}:
            root.mkdir(parents=True, exist_ok=True)
        for action in pending:
            action["stage"] = prepare_stage(
                action["row"], action["root"], action["target"], keep_extra)
        commit_stages(pending)
    except Exception:
        for action in actions:
            stage = action.get("stage")
            if stage is not None:
                remove_generated_tree(stage)
        raise
    return actions


def main() -> int:
    parser = argparse.ArgumentParser(
        description="安装 ICODE manifest 声明的共享技能")
    parser.add_argument("--manifest")
    parser.add_argument("--target-root", action="append")
    parser.add_argument("--component-root", type=Path)
    parser.add_argument("--component")
    parser.add_argument("--component-archive", type=Path, help="Offline archive; omit to obtain the pinned release automatically")
    parser.add_argument("--component-cache", type=Path, help="Optional private cache root; discovery files are never expanded in place")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-extra", action="store_true",
                        help="更新已托管技能时保留目标独有文件")
    args = parser.parse_args()
    if args.component_root is not None or args.component is not None or args.component_archive is not None or args.component_cache is not None:
        if not args.component_root or not args.component or args.manifest or args.target_root or args.keep_extra:
            parser.error("component mode requires --component-root and --component; shared-skill options cannot be mixed")
        try:
            result = install_component(args.component_root, args.component, args.component_archive, args.dry_run, args.component_cache)
        except (InstallConflict, OSError, ValueError, UnicodeError, zipfile.BadZipFile) as exc:
            # HTTP errors may contain signed redirect URLs. Keep their values local.
            message = str(exc) if isinstance(exc, InstallConflict) else type(exc).__name__
            print(json.dumps({"ok": False, "error": message}, ensure_ascii=True), file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=True))
        return 0
    if not args.manifest or not args.target_root:
        parser.error("shared skill mode requires --manifest and --target-root")
    try:
        actions = install(
            Path(args.manifest).expanduser().resolve(),
            args.target_root,
            args.dry_run,
            args.keep_extra,
        )
    except (InstallConflict, OSError, UnicodeError, ValidationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    for action in actions:
        prefix = "would " if args.dry_run else ""
        print(f"{prefix}{action['action']}: {action['target']}")
    print(json.dumps({
        "ok": True,
        "dry_run": args.dry_run,
        "targets": len(actions),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
