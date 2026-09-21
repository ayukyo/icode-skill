#!/usr/bin/env python3
"""Read-only, offline SHA-256 verification of installed ICODE root resources."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

# A normal invocation must not create bytecode beside the reused source tools.
sys.dont_write_bytecode = True
from build_skill_distribution import METADATA, no_symlinks, source_files


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


@contextmanager
def open_directory(path):
    """Pin each ancestor without following links, including during payload reads."""
    no_symlinks(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def sha256_file(path):
    no_symlinks(path)
    with open_directory(path.parent) as parent:
        if not stat.S_ISREG(os.stat(path.name, dir_fd=parent, follow_symlinks=False).st_mode):
            raise ValueError(f"non-regular file: {path}")
        # NONBLOCK avoids hanging if a regular file is swapped for a FIFO.
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError(f"non-regular file: {path}")
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            return digest.hexdigest()


def inspect_extra_links(installed, paths, report):
    """Inspect installation metadata only; never open extra files or link targets."""
    covered = set(paths)
    covered.update(parent for path in paths for parent in path.parents)
    pending = [Path(".")]
    while pending:
        relative = pending.pop()
        try:
            with open_directory(installed / relative) as descriptor:
                with os.scandir(descriptor) as entries:
                    for entry in sorted(entries, key=lambda item: item.name):
                        child = relative / entry.name
                        if entry.is_symlink():
                            # Expected resources already report the full required
                            # file path, including when an ancestor is a link.
                            if child not in covered:
                                report["unsafe"].append({"path": child.as_posix(),
                                                         "reason": "symlink is not allowed"})
                        elif entry.is_dir(follow_symlinks=False):
                            pending.append(child)
        except (OSError, ValueError) as exc:
            report["errors"].append(f"{relative.as_posix()}: {exc}")


def check_installation(source, installed):
    report = {
        "ok": False, "scope": "root_workflow_resources",
        "source": str(source), "installed": str(installed),
        "expected_files": 0, "checked_files": 0,
        "missing": [], "changed": [], "unsafe": [], "errors": [],
    }
    try:
        source = source.expanduser().absolute()
        no_symlinks(source)
        with open_directory(source):
            pass
        # The real distribution selector preflights ALL selected paths before
        # hashing any payload. Never recursively read private/untracked sources.
        paths = [p for p in source_files(source) if p.as_posix() not in METADATA]
        report["source"] = str(source)
        report["expected_files"] = len(paths)
        expected = {p: sha256_file(source / p) for p in paths}
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        report["errors"].append(str(exc))
        return report, 2

    try:
        installed = installed.expanduser().absolute()
        report["installed"] = str(installed)
        with open_directory(installed):
            pass
    except ValueError as exc:
        report["unsafe"].append({"path": ".", "reason": str(exc)})
        return report, 1
    except (OSError, RuntimeError) as exc:
        report["errors"].append(str(exc))
        return report, 1

    for relative, source_hash in expected.items():
        name = relative.as_posix()
        try:
            installed_hash = sha256_file(installed / relative)
        except FileNotFoundError:
            report["missing"].append(name)
            continue
        except (ValueError, NotADirectoryError) as exc:
            report["unsafe"].append({"path": name, "reason": str(exc)})
            continue
        except OSError as exc:
            report["errors"].append(f"{name}: {exc}")
            continue
        report["checked_files"] += 1
        if installed_hash != source_hash:
            report["changed"].append({"path": name, "source_sha256": source_hash,
                                      "installed_sha256": installed_hash})
    inspect_extra_links(installed, paths, report)
    report["unsafe"].sort(key=lambda item: item["path"])
    report["ok"] = not any(report[key] for key in ("missing", "changed", "unsafe", "errors"))
    return report, 0 if report["ok"] else 1


def main():
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Source Git checkout root")
    parser.add_argument("--installed", required=True,
                        help="Installed root containing SKILL.md, steps/, tools/, etc.")
    try:
        args = parser.parse_args()
        if not args.source or not args.installed:
            raise ValueError("--source and --installed must be non-empty paths")
    except ValueError as exc:
        print(json.dumps({"ok": False, "scope": "root_workflow_resources",
                          "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    report, code = check_installation(Path(args.source), Path(args.installed))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
