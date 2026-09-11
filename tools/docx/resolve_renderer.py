#!/usr/bin/env python3
"""Resolve only a hash-verified ICODE-owned DOCX renderer bundle.

This intentionally never calls ``soffice`` from PATH.  A distro LibreOffice is
not a stable renderer ABI and has already failed in real use due to glibc
mismatch.  Release packaging adds renderer records when binaries are available.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path


TOOL_DIR = Path(__file__).resolve().parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def host() -> dict[str, str]:
    libc_name, libc_version = platform.libc_ver()
    return {
        "os": platform.system().lower(),
        "arch": platform.machine().lower(),
        "libc": libc_name.lower(),
        "glibc": libc_version,
    }


def version_at_least(actual: str, minimum: str | None) -> bool:
    if not minimum:
        return True
    try:
        return tuple(map(int, actual.split("."))) >= tuple(map(int, minimum.split(".")))
    except ValueError:
        return False


def resolve(manifest_path: Path) -> dict:
    current = host()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "invalid_manifest", "host": current, "reason": str(exc)}
    for entry in manifest.get("renderers", []):
        if entry.get("os") != current["os"] or entry.get("arch") != current["arch"]:
            continue
        if entry.get("libc") == "glibc" and not version_at_least(current["glibc"], entry.get("glibc_min")):
            continue
        bundle = (manifest_path.parent / entry.get("bundle", "")).resolve()
        soffice = bundle / entry.get("soffice", "soffice")
        pdftoppm = bundle / entry.get("pdftoppm", "pdftoppm")
        expected = entry.get("sha256", {})
        for label, path in (("soffice", soffice), ("pdftoppm", pdftoppm)):
            if not path.is_file() or not path.stat().st_mode & 0o111:
                return {"status": "invalid_bundle", "host": current, "reason": f"{label} 不存在或不可执行: {path}"}
            if expected.get(label) and sha256_file(path) != expected[label]:
                return {"status": "invalid_bundle", "host": current, "reason": f"{label} SHA-256 不匹配"}
        return {
            "status": "ready",
            "host": current,
            "renderer": {"id": entry.get("id"), "bundle": str(bundle), "soffice": str(soffice), "pdftoppm": str(pdftoppm)},
        }
    return {
        "status": "unavailable",
        "host": current,
        "reason": "未随 ICODE 发行包提供此 OS/CPU/glibc 的受管理 DOCX 渲染器",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="resolve an ICODE-owned DOCX renderer")
    parser.add_argument("--manifest", type=Path, default=TOOL_DIR / "renderer_manifest.json")
    args = parser.parse_args()
    print(json.dumps(resolve(args.manifest), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
