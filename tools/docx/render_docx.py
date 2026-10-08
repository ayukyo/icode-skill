#!/usr/bin/env python3
"""Visual-render a DOCX only with an ICODE-owned compatible renderer."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from resolve_renderer import TOOL_DIR, resolve
from bootstrap_renderer import bootstrap, CATALOG
from renderer_support import runtime_root


def update_manifest(path: Path, status: str, **extra: object) -> None:
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data["visual_qa"] = {"status": status, **extra}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="render DOCX with ICODE-owned renderer")
    parser.add_argument("docx", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--manifest", type=Path, help="use only this renderer manifest; disable automatic installation")
    parser.add_argument("--result-manifest", type=Path)
    parser.add_argument("--dpi", type=int, default=144)
    args = parser.parse_args()
    if not args.docx.is_file() or args.dpi <= 0:
        parser.error("DOCX must exist and DPI must be positive")
    resolved = resolve(args.manifest)
    # First-use and public installation share the exact same locked installer.
    # Explicit manifests are hermetic (including unsupported-platform fixtures).
    if args.manifest is None and resolved["status"] == "unavailable":
        try:
            root = runtime_root().expanduser().resolve()
            installed = bootstrap(CATALOG, root, root / "downloads")
            if installed["status"] in ("ready", "installed"):
                resolved = resolve()
        except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as exc:
            resolved = {"status": "install_failed", "reason": str(exc)}
    if resolved["status"] != "ready":
        failed = resolved["status"] != "unavailable"
        status = "visual_qa_failed" if failed else "visual_qa_pending"
        if args.result_manifest:
            update_manifest(args.result_manifest, status, resolver=resolved)
        print(json.dumps({"status": status, "resolver": resolved}, ensure_ascii=False))
        return 1 if failed else 0
    args.out_dir.mkdir(parents=True, exist_ok=True)
    renderer = resolved["renderer"]
    try:
        with tempfile.TemporaryDirectory(prefix="icode-docx-render-") as temporary:
            temp = Path(temporary)
            subprocess.run([renderer["soffice"], "--headless", "--norestore", "--nologo", "--nofirststartwizard", "--convert-to", "pdf", "--outdir", str(temp), str(args.docx)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
            pdfs = list(temp.glob("*.pdf"))
            if len(pdfs) != 1:
                raise RuntimeError("renderer did not produce exactly one PDF")
            # Collect this invocation's pages in a fresh directory to avoid stale pages.
            subprocess.run([renderer["pdftoppm"], "-png", "-r", str(args.dpi), str(pdfs[0]), str(temp / "page")], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
            generated = sorted(temp.glob("page-*.png"), key=lambda path: int(path.stem.split("-")[-1]))
            pages = []
            for path in generated:
                target = args.out_dir / path.name
                target.write_bytes(path.read_bytes())
                pages.append(str(target))
        if not pages:
            raise RuntimeError("renderer did not produce PNG pages")
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        detail = str(exc)
        stderr = getattr(exc, "stderr", None)
        if stderr:
            detail += ": " + (stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr)[-2000:]
        if args.result_manifest:
            update_manifest(args.result_manifest, "visual_qa_failed", resolver=resolved, error=detail)
        print(f"ERROR: DOCX visual render failed: {detail}", file=sys.stderr)
        return 1
    if args.result_manifest:
        update_manifest(args.result_manifest, "passed", renderer=renderer, pages=pages)
    print(json.dumps({"status": "passed", "pages": pages, "renderer": renderer}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
