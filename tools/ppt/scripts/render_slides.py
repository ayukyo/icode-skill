#!/usr/bin/env python3
"""Render every slide of a .pptx to PNG files using LibreOffice + pdftoppm.

Usage:
    python3 render_slides.py <input.pptx> <out_dir> [--dpi 144]

Output:
    <out_dir>/slide-<N>.png  (1-indexed, zero-padded)
"""
from __future__ import annotations
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def render(pptx: Path, out_dir: Path, dpi: int = 144) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pptrender_") as td:
        td_path = Path(td)
        subprocess.run(
            [
                "soffice",
                "--headless",
                "--norestore",
                "--nologo",
                "--nofirststartwizard",
                "--convert-to",
                "pdf",
                "--outdir",
                str(td_path),
                str(pptx),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        pdfs = list(td_path.glob("*.pdf"))
        if not pdfs:
            raise RuntimeError("soffice did not produce a PDF")
        pdf = pdfs[0]
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-r",
                str(dpi),
                str(pdf),
                str(out_dir / "slide"),
            ],
            check=True,
        )
    pngs = sorted(out_dir.glob("slide-*.png"))
    return pngs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--dpi", type=int, default=144)
    args = ap.parse_args()
    pngs = render(args.pptx, args.out_dir, args.dpi)
    print(f"Rendered {len(pngs)} slides → {args.out_dir}")
    for p in pngs:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
