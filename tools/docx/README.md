# ICODE DOCX runtime

`tools/docx/` is the self-managed local implementation behind `/icode docx`.
It is not an MCP server: generating OOXML is deterministic local work, while
existing MCPs remain responsible only for optional evidence/index retrieval.

## Runtime ownership

Run the bootstrapper once (the public `./install.sh` does this automatically):

```bash
python3 tools/docx/bootstrap_runtime.py
python3 tools/docx/bootstrap_renderer.py
```

It creates a lock-content-addressed venv under
`~/.local/share/icode/runtime/docx/<lock-hash>/`.  The source lock pins the
third-party packages; no global `pip`, `sudo`, preinstalled `python-docx`, or
host LibreOffice is used.  An offline release may add wheels under
`tools/docx/wheels/`; otherwise first installation needs normal package-index
network access.  With no pip index configured, the bootstrapper tries PyPI and
only retries a network/timeout failure through its built-in HTTPS mirrors
(Tsinghua, then Aliyun), always against the same lock.  A caller-provided
`PIP_INDEX_URL`, `PIP_EXTRA_INDEX_URL`, or `PIP_NO_INDEX` is respected exactly:
ICODE neither overrides it nor records its URL (which may contain credentials).
Dependency/version failures never switch sources.

## Build and inspect

```bash
RUNTIME_JSON="$(python3 tools/docx/bootstrap_runtime.py)"
PYTHON="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["python"])' "$RUNTIME_JSON")"
"$PYTHON" tools/docx/build_docx.py guide.md guide.docx --manifest guide.manifest.json
"$PYTHON" tools/docx/inspect_docx.py guide.docx --source guide.md --manifest guide.manifest.json
"$PYTHON" tools/docx/render_docx.py guide.docx preview --result-manifest guide.manifest.json
```

The builder supports ICODE Markdown: headings, paragraphs, lists, tables,
quotes, code blocks, local images, and common Mermaid flowcharts.  Mermaid
flowcharts become PNG images embedded in DOCX.  Unsupported Mermaid stays as a
code block and is recorded in the source map; it is never silently discarded.

## Visual QA contract

The public installer and first default render both call `bootstrap_renderer.py`.
`renderer_packages.lock.json` pins official HTTPS archives, exact sizes and
SHA-256; installation extracts owned binaries without sudo or maintainer scripts.
The current recipe targets Linux x86_64, glibc >= 2.35, kernel >= 4.18 and
x86-64-v2 CPU flags. It was validated on Ubuntu 22.04; general shared libraries,
`dpkg-deb`, `ldd`, and fonts remain host prerequisites. Missing libraries fail
installation with a specific error; unsupported OS/CPU combinations stay
`visual_qa_pending`. Windows, macOS and ARM renderer recipes are not supplied.

Bundles and their atomic registry live under
`~/.local/share/icode/runtime/docx-renderer/`, outside synchronized skill trees.
Default resolution reads the shipped manifest and this persistent registry;
`--manifest` explicitly selects just one manifest and disables auto-install.
Entrypoints and payload executable/library hashes are checked; invocation uses
an isolated temporary Office profile and bounded subprocess timeouts. There is
**never** a PATH LibreOffice/Poppler fallback. Broken or incompatible existing
bundles are not overwritten and cannot be reported as ready.

Read-only check: `python3 tools/docx/bootstrap_renderer.py --check`.
Offline install: `python3 tools/docx/bootstrap_renderer.py --offline --package-cache /path/to/archives`.
Use the exact artifact filenames in the lock; offline/cache inputs are rehashed.
`ICODE_DOCX_RENDERER_ROOT` relocates user state; `ICODE_DOCX_RENDERER_CATALOG`
selects an explicit administrator-controlled catalog for the bootstrap CLI.
No personal path belongs in the shipped lock or manifest.

The distribution builder requires the installer, launcher, validator and lock
as a complete set. `.github/workflows/docx-renderer.yml` qualifies source/release
snapshots through public install, sync, and real Word-to-page smoke rendering.
The public-site artifact build depends on this reusable qualification workflow;
failed renderer qualification prevents distribution upload and deployment.
A local equivalent is `python3 scripts/check-docx-renderer-release.py --output-dir /new/report/dir`.
Rendering success is separate from a human visual inspection of every page.
