#!/usr/bin/env python3
"""Build one offline Release attachment batch; never tag, upload or publish."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

sys.dont_write_bytecode = True
from build_skill_distribution import (
    ROOT, CORE_MAX_BYTES, CORE_MAX_FILES, CORE_ENTRY_MAX_BYTES,
    build, file_inventory, no_symlinks,
)
from prepare_skill_listing import drafts

# A conservative local release policy, not a claim of registry approval.
SKILLHUB_TEXT_SUFFIXES = {".md", ".txt", ".json", ".py", ".sh", ".svg"}
SKILLHUB_OPTIONAL_UI = {"agents/openai.yaml", "assets/icode-ticket-hex-128.png",
                       "assets/icode-ticket-hex-512.png", "assets/icode-ticket-hex.manifest.json"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(root):
    return [path for path in sorted(root.rglob("*")) if path.is_file()]


def zip_tree(root, target):
    with zipfile.ZipFile(target, "w") as archive:
        for path in files(root):
            info = zipfile.ZipInfo("icode/" + path.relative_to(root).as_posix(),
                                   date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED,
                             compresslevel=9)


def tar_tree(root, target):
    with target.open("xb") as stream:
        with gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in files(root):
                    info = tarfile.TarInfo("icode/" + path.relative_to(root).as_posix())
                    info.size = path.stat().st_size
                    info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                    with path.open("rb") as payload:
                        archive.addfile(info, payload)


def verify_batch(full, core, components, version):
    """Reject source changes between builds instead of mixing two snapshots."""
    manifest = json.loads((core / "components.json").read_text(encoding="utf-8"))
    if manifest["version"] != version:
        raise ValueError("component/core/full version mismatch")
    restored = {row["path"]: row["sha256"] for row in manifest["core_files"]}
    restored.pop("SKILL.md")
    restored["SKILL.md"] = restored.pop("WORKFLOW.md")
    for component in manifest["components"].values():
        archive = components / component["archive"]
        if archive.stat().st_size != component["size"] or digest(archive) != component["sha256"]:
            raise ValueError("component attachment integrity mismatch")
        for row in component["files"]:
            if row["path"] in restored:
                raise ValueError("duplicate resource across release profiles")
            restored[row["path"]] = row["sha256"]
    expected = {path.relative_to(full).as_posix(): digest(path) for path in files(full)}
    if restored != expected:
        raise ValueError("core and components do not match the full source snapshot")


def skillhub_snapshot(core, output, version):
    """Project the same frozen core into one text-only, pinned upload skill."""
    shutil.copytree(core, output)
    for name in SKILLHUB_OPTIONAL_UI:
        path = output / name
        if path.is_file():
            path.unlink()  # Only this build's private temporary projection.
    metadata = drafts(core, "skillhub")
    if metadata["source_version"] != version:
        raise ValueError("SkillHub/core version mismatch")
    fields = metadata["drafts"][0]["frontmatter"]
    entry = (output / "SKILL.md").read_text(encoding="utf-8")
    header = "".join(name + ": " + json.dumps(fields[name], ensure_ascii=False) + "\n"
                     for name in ("slug", "version", "displayName", "summary", "tags", "homepage"))
    (output / "SKILL.md").write_text("---\n" + header + entry[4:], encoding="utf-8", newline="\n")
    manifest = json.loads((output / "components.json").read_text(encoding="utf-8"))
    manifest["core_files"] = file_inventory(output)
    (output / "components.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    payload = files(output)
    if not (output / "LICENSE.txt").is_file():
        raise ValueError("SkillHub package must preserve LICENSE.txt")
    for path in payload:
        if path.suffix.lower() not in SKILLHUB_TEXT_SUFFIXES:
            raise ValueError("SkillHub unsupported file type: " + path.relative_to(output).as_posix())
        path.read_text(encoding="utf-8", errors="strict")
    size = sum(path.stat().st_size for path in payload)
    if (len(payload) > CORE_MAX_FILES or size > CORE_MAX_BYTES
            or (output / "SKILL.md").stat().st_size > CORE_ENTRY_MAX_BYTES):
        raise ValueError("SkillHub core budget exceeded")
    return {"files": len(payload), "bytes": size, "skills": 1}


def build_assets(source, output):
    source, output = source.expanduser().absolute(), output.expanduser().absolute()
    no_symlinks(source)
    no_symlinks(output)
    if output.exists() or output.is_relative_to(source) or not output.parent.is_dir():
        raise ValueError("release output must be a new directory outside the source with an existing parent")
    with tempfile.TemporaryDirectory(prefix="icode-release-") as temporary:
        work = Path(temporary)
        for name in ("full", "core", "assets"):
            (work / name).mkdir()
        full = work / "full/icode"
        core = work / "core/icode"
        components = work / "components"
        full_report = build(source, full)
        core_report = build(source, core, "core", components)
        version = full_report["version"]
        if core_report["version"] != version:
            raise ValueError("source version changed during release build")
        verify_batch(full / "skills/icode", core / "skills/icode", components, version)
        # Shared skills and plugin metadata must also come from the same batch.
        def plugin_resources(root):
            return {path.relative_to(root).as_posix(): digest(path) for path in files(root)
                    if not path.is_relative_to(root / "skills/icode")
                    and path != root / "SHA256SUMS"}
        if plugin_resources(full) != plugin_resources(core):
            raise ValueError("plugin resources changed between release profile builds")
        skillhub = work / "skillhub/icode"
        skillhub_report = skillhub_snapshot(core / "skills/icode", skillhub, version)
        assets = work / "assets"
        for path in files(components):
            shutil.copyfile(path, assets / path.name)
        tar_tree(full, assets / f"icode-v{version}.tar.gz")
        zip_tree(core / "skills/icode", assets / f"icode-v{version}-core.zip")
        zip_tree(core, assets / f"icode-v{version}-plugin-core.zip")
        zip_tree(skillhub, assets / f"icode-v{version}-skillhub.zip")
        rows = [{"name": path.name, "size": path.stat().st_size, "sha256": digest(path)}
                for path in files(assets)]
        record = {"schema_version": 1, "version": version, "assets": rows,
                  "profiles": {name: {key: report[key] for key in ("files", "bytes", "skills")}
                               for name, report in (("full", full_report), ("core", core_report),
                                                    ("skillhub", skillhub_report))},
                  "validation": "offline_payload_integrity", "published": False}
        tarball = assets / f"icode-v{version}.tar.gz"
        (assets / (tarball.name + ".sha256")).write_text(
            f"{digest(tarball)}  {tarball.name}\n", encoding="utf-8")
        (assets / "release-assets.json").write_text(
            json.dumps(record, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        sums = [f"{digest(path)}  {path.name}\n" for path in files(assets)]
        (assets / "SHA256SUMS").write_text("".join(sums), encoding="utf-8")
        # Validate the entire batch before reserving any public output path.
        output.mkdir()
        for path in files(assets):
            with (output / path.name).open("xb") as target:
                target.write(path.read_bytes())
    return {"output": str(output), **record}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build_assets(args.source, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
