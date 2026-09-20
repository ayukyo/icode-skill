#!/usr/bin/env python3
"""Build an offline skills-only plugin snapshot; never install or publish it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from validate_skill_pack import iter_source_publish_files, validate_routes, validate_skills


ROOT = Path(__file__).resolve().parents[1]
HOSTS = ("claude", "codex", "codebuddy")
# Runtime resources only. Website, tests, demo, host settings and Git are excluded.
DIRECTORIES = {"steps", "references", "tools", "schemas", "templates", "scripts",
               "mcp", "agent_runtime", "skill-packs", "docs"}
FILES = {"SKILL.md", "LICENSE", "README.md", "README.zh-CN.md", "install.sh", ".gitignore"}
METADATA = {"integrations/discovery/README.md"} | {
    f"integrations/discovery/{host}.plugin.json.template" for host in HOSTS}
REQUIRED = FILES | METADATA | {"skill-packs/manifest.json", "mcp/workflow-gate/gates.json",
                    "mcp/workflow-gate/skill-routes.json", "tools/icode_control.py",
                    "scripts/sync-to-global.sh"}


def no_symlinks(path):
    if ".." in path.parts:
        raise ValueError(f"parent traversal is not allowed: {path}")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"symlink is not allowed: {component}")


def allowed(path):
    return (path.as_posix() in FILES | METADATA or path.parts[0] in DIRECTORIES
            or path.parts[:2] == ("integrations", "codebuddy")) \
        and path.as_posix() != "tools/build_skill_distribution.py"


def check_public_path(path):
    for part in path.parts:
        lower = part.lower()
        if lower.startswith(".") and path.as_posix() != ".gitignore":
            raise ValueError(f"hidden/private path is not publishable: {path}")
        if (re.search(r"(^|[._-])(private|secrets?|credentials?)([._-]|$)", lower)
                or lower in {"__pycache__", "node_modules"}):
            raise ValueError(f"private/runtime path is not publishable: {path}")
    name = path.name.lower()
    # Only these reviewed public cookie tools are payload, never their cookie data.
    if path.as_posix() in {"tools/tb/scripts/tb_cookie.py", "tools/tb/scripts/tb_cookie_win.py"}:
        return
    if any(re.search(r"(^|[._-])(cookies?|token)([._-]|$)", part.lower()) for part in path.parts):
        raise ValueError(f"credential data path is not publishable: {path}")
    if name == "config.example.json" or name.endswith(".template"):
        return
    if (re.search(r"(^|[._-])(private|secrets?|credentials?|cookies?|token)([._-]|$)", name)
            or re.match(r"config([._-]|$)", name)
            or path.suffix.lower() in {".pem", ".key", ".p12", ".pyc", ".pyo", ".db", ".sqlite"}):
        raise ValueError(f"sensitive/config path is not publishable: {path}")


def source_files(source):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], stderr=subprocess.PIPE)
    if Path(git("rev-parse", "--show-toplevel").decode().strip()) != source:
        raise ValueError("--source must be the Git root")
    selected = []
    for row in git("ls-files", "--stage", "-z").decode().split("\0"):
        if not row:
            continue
        metadata, name = row.split("\t", 1)
        mode, _, stage = metadata.split()
        path = Path(name)
        if not allowed(path):
            continue
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"invalid source path: {name}")
        check_public_path(path)
        no_symlinks(source / path)
        if mode not in {"100644", "100755"} or stage != "0":
            raise ValueError(f"non-regular/unmerged Git entry: {name}")
        if not stat.S_ISREG((source / path).stat().st_mode):
            raise ValueError(f"non-regular source file: {name}")
        selected.append(path)
    missing = REQUIRED - {p.as_posix() for p in selected}
    missing |= {f"{d}/" for d in DIRECTORIES if not any(p.parts[0] == d for p in selected)}
    if missing:
        raise ValueError(f"missing workflow resources: {sorted(missing)}")
    return sorted(selected)


def build(source, output):
    source, output = source.expanduser().absolute(), output.expanduser().absolute()
    no_symlinks(source)
    no_symlinks(output)
    if output.exists() or output.is_relative_to(source) or output.name != "icode":
        raise ValueError("output must be a new directory named icode outside the source")
    if not output.parent.is_dir():
        raise ValueError("output parent must already exist")
    paths = source_files(source)  # Preflight every path before reading any payload.
    templates = source / "integrations/discovery"
    for name in ("README.md", *(f"{host}.plugin.json.template" for host in HOSTS)):
        no_symlinks(templates / name)
    # Validate a tracked-only snapshot: the existing shared-skill validator must
    # never traverse untracked files or personal configuration in the checkout.
    with tempfile.TemporaryDirectory(prefix="icode-distribution-") as temporary:
        package = Path(temporary) / "icode"
        workflow = package / "skills/icode"
        for rel in paths:
            if rel.as_posix() in METADATA:
                continue
            target = workflow / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / rel, target)
            target.chmod(0o755 if (source / rel).stat().st_mode & 0o111 else 0o644)
        version = re.search(r"^\*\*版本\*\*:\s*v(\d+\.\d+\.\d+)\s*$",
                            (workflow / "SKILL.md").read_text(), re.MULTILINE)
        if not version:
            raise ValueError("root SKILL.md must declare a release version")
        rows = validate_skills(workflow / "skill-packs/manifest.json")
        if not rows or any(row["name"] == "icode" for row in rows):
            raise ValueError("shared skill manifest is empty or collides with icode")
        validate_routes(workflow / "mcp/workflow-gate/skill-routes.json",
                        {row["name"] for row in rows})
        for row in rows:
            for origin, rel in iter_source_publish_files(Path(row["path"]), row["entrypoint"]):
                target = package / "skills" / row["name"] / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, target)
                target.chmod(origin.stat().st_mode & 0o777)
        for host in HOSTS:
            manifest = json.loads((templates / f"{host}.plugin.json.template").read_text())
            manifest["version"] = version.group(1)
            target = package / f".{host}-plugin/plugin.json"
            target.parent.mkdir()
            target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        shutil.copyfile(templates / "README.md", package / "README.md")
        shutil.copyfile(workflow / "LICENSE", package / "LICENSE")
        sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(package).as_posix()}"
                for p in sorted(package.rglob("*")) if p.is_file()]
        (package / "SHA256SUMS").write_text("\n".join(sums) + "\n")
        # Exclusive creation; no overwrite/merge option. A disk failure leaves
        # an incomplete new output for inspection, never deletes user data.
        output.mkdir()
        shutil.copytree(package, output, dirs_exist_ok=True)
    return {"output": str(output), "version": version.group(1), "skills": 1 + len(rows),
            "validation": "package_structure_only", "host_install_tested": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(args.source, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
