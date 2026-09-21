#!/usr/bin/env python3
"""Render the canonical ICODE SVG into reviewed PNG derivatives.

This is a maintainer tool, not a runtime dependency. It records the exact
source/output hashes and renderer version so binary drift is explicit in review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets/icode-ticket-hex.svg"
MANIFEST = ROOT / "assets/icode-ticket-hex.manifest.json"
OUTPUTS = {
    128: ROOT / "assets/icode-ticket-hex-128.png",
    512: ROOT / "assets/icode-ticket-hex-512.png",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def png_contract(path: Path, expected_size: int) -> None:
    payload = path.read_bytes()
    if payload[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise RuntimeError(f"renderer did not produce a PNG: {path}")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[16:26])
    if (width, height, bit_depth, color_type) != (expected_size, expected_size, 8, 6):
        raise RuntimeError(
            f"unexpected PNG contract for {path}: "
            f"{width}x{height}, depth={bit_depth}, color_type={color_type}"
        )


def find_chrome(explicit: str | None) -> str:
    if explicit:
        candidate = shutil.which(explicit) if os.sep not in explicit else explicit
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
        raise RuntimeError(f"Chrome executable not found: {explicit}")
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        candidate = shutil.which(name)
        if candidate:
            return candidate
    raise RuntimeError("Chrome/Chromium is required to regenerate brand PNG assets")


def renderer_version(chrome: str) -> str:
    result = subprocess.run(
        [chrome, "--version"], check=True, capture_output=True, text=True, timeout=10
    )
    value = result.stdout.strip()
    for prefix in ("Google Chrome ", "Chromium "):
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def render(chrome: str) -> dict:
    if not SOURCE.is_file():
        raise RuntimeError(f"canonical SVG is missing: {SOURCE}")
    version = renderer_version(chrome)
    # Keep temporary outputs on the assets filesystem so os.replace remains atomic.
    with tempfile.TemporaryDirectory(prefix=".icode-brand-", dir=SOURCE.parent) as temporary:
        temp = Path(temporary)
        generated = {}
        for size, destination in OUTPUTS.items():
            output = temp / destination.name
            profile = temp / f"chrome-{size}"
            command = [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                "--default-background-color=00000000",
                f"--user-data-dir={profile}",
                f"--window-size={size},{size}",
                f"--screenshot={output}",
                SOURCE.resolve().as_uri(),
            ]
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                command.insert(1, "--no-sandbox")
            subprocess.run(command, check=True, capture_output=True, timeout=30)
            png_contract(output, size)
            generated[size] = output

        for size, destination in OUTPUTS.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(generated[size], destination)

    manifest = {
        "schema_version": 1,
        "source": {
            "path": SOURCE.relative_to(ROOT).as_posix(),
            "sha256": digest(SOURCE),
        },
        "renderer": {"name": "Google Chrome", "version": version},
        "outputs": {
            path.relative_to(ROOT).as_posix(): {
                "sha256": digest(path),
                "width": size,
                "height": size,
                "mode": "RGBA",
            }
            for size, path in OUTPUTS.items()
        },
    }
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_manifest = MANIFEST.with_suffix(".json.tmp")
    temporary_manifest.write_text(payload, encoding="utf-8")
    os.replace(temporary_manifest, MANIFEST)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", help="Chrome/Chromium executable; auto-detected by default")
    arguments = parser.parse_args()
    manifest = render(find_chrome(arguments.chrome))
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
