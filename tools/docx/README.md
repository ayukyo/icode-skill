# ICODE DOCX runtime

`tools/docx/` is the self-managed local implementation behind `/icode docx`.
It is not an MCP server: generating OOXML is deterministic local work, while
existing MCPs remain responsible only for optional evidence/index retrieval.

## Runtime ownership

Run the bootstrapper once (the public `./install.sh` does this automatically):

```bash
python3 tools/docx/bootstrap_runtime.py
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

`renderer_manifest.json` only selects ICODE-owned renderer bundles by OS, CPU
architecture, and glibc requirement, then verifies executable SHA-256.  It
will **never** fall back to a `soffice` found on PATH.  If the release contains
no compatible renderer, `render_docx.py` writes `visual_qa_pending` to the
delivery manifest without claiming a visual pass.  A release adds compatible
binaries under `tools/docx/renderers/` and a matching manifest record; no
workflow or host setup change is required.
