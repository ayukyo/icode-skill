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

Technical diagrams can retain one local SVG source. The managed Python runtime
pins resvg-py 0.5.0 and fonttools 4.60.1; no browser is required. Text uses
explicit local fonts (`--svg-font`, `ICODE_SVG_FONT_FILES`, or named CJK
candidates in `diagram_svg.py`); missing glyphs fail. Fonts are not bundled or
downloaded. Static shapes, paths, groups, text/tspan and local markers work;
CSS styles, scripts, events, DTD/entities, external resources, images,
use-elements and foreignObject fail. A positive viewBox is required; explicit
width/height must match its aspect ratio.

```bash
"$PYTHON" tools/docx/diagram_svg.py figure.svg --validate-only
"$PYTHON" tools/docx/build_docx.py guide.md guide.docx --svg-dpi 192
"$PYTHON" tools/docx/build_docx.py guide.md guide-dual.docx --svg-mode dual
```

Default output is `raster_fallback`, sized by physical width and DPI. Dual mode
keeps SVG plus PNG and reports `native_vector_pending`; `vector_preserved=true`
describes the packaged source, not verified Office behavior. Source, PNG and
receipts remain with the delivery; records follow `diagram.schema.json`.
Cache identity includes source, renderer, font hashes, width and DPI. Inspection
separately checks source/embedded hashes, SVG content types and relationships,
drawing identity, alt text and ratio. Acceptance uses the open-source resvg +
managed LibreOffice chain with local open fonts such as Noto CJK: actual import,
PDF export, resave, fallback and final page review. Microsoft Office and WPS are
optional compatibility checks, never installation dependencies or release gates.
`draw_sequence.py` also accepts `.svg` output with native text elements.

Prepare both DOCX and PPT components to generate three Chinese qualification
diagrams and four raster/dual packages with `qualify_svg.py --output-dir <new
external directory> --font /path/to/NotoSansCJK-Regular.ttc`. Its receipt records
build evidence only; actual LibreOffice and page-review results are separate.

Run `check_svg_release.py --fixtures <fixture-directory> --output-dir <new
consumer-directory>` with the same managed Python. It checks every artifact
hash before executing the pinned owned renderer, exports and resaves all four
packages, and requires three pages before/after. Dual mode must preserve the
three source SVG hashes and PNG fallbacks. Verified owned `pdfimages` checks
that dual PDFs contain no raster image objects, while raster PDFs contain the
expected images. Rendering and roundtrip statuses can pass automatically;
`visual_review` stays pending until all final pages have actually been viewed.

```bash
RUNTIME_JSON="$(python3 tools/docx/bootstrap_runtime.py)"
PYTHON="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["python"])' "$RUNTIME_JSON")"
"$PYTHON" tools/docx/build_docx.py guide.md guide.docx --manifest guide.manifest.json
"$PYTHON" tools/docx/inspect_docx.py guide.docx --source guide.md --manifest guide.manifest.json
"$PYTHON" tools/docx/render_docx.py guide.docx preview --result-manifest guide.manifest.json
```

The builder supports headings, paragraphs, lists, tables, quotes, code blocks,
local images and a deliberately narrow Mermaid subset: a single linear path,
rectangular nodes and unlabeled solid arrows, in LR/RL/TB/BT/TD directions.
Each statement is checked before drawing; branches, conditions, edge labels,
styles and unsupported shapes retain the entire source as code with a warning.
Use local diagram images for these cases rather than silently simplifying them.
`inspect_docx.py --require-diagrams` rejects code fallbacks in authored deliveries.

Inspection checks actual heading text/styles/order, image relationships and
input/output/source-image hashes. Reusing an image in multiple places is valid.
Syntax inside code fences is excluded from structural expectations. These
checks verify artifact integrity, not the correctness of an explanation.

## Authoring and existing Word revisions

Read [the workflow](../../steps/docx.md), then load only the relevant
[authoring/diagram guide](../../references/docx_authoring.md) or
[revision guide](../../references/docx_revision.md). The host agent handles
chapter planning, prose, source/call-chain review and diagram semantics.
Complex figures or layout may use available document/drawing capabilities or
local scripts using the managed Python's `python-docx` and Pillow. Retain the
source, scripts, shared module palette and manifest; use the same final checks.
Images are fitted to both printable width and height without changing aspect
ratio; the agent still checks whether scaling makes diagram labels too small.

```bash
"$PYTHON" tools/docx/verify_sources.py sources.json --root /path/to/project --report sources.qa.json
"$PYTHON" tools/docx/draw_sequence.py sequence.json sequence.png --palette palette.json --manifest sequence.manifest.json
"$PYTHON" tools/docx/compare_docx.py before.docx candidate.docx \
  --allow-section "5.1 Coordinate conversion" --allow-figure 5 --report revision.qa.json
```

Source records have a nonempty `files` list, each with a relative `path`, the
reviewed file's lowercase `sha256`, and nonempty `anchors` containing 1-based
`start`/`end` lines and literal `contains` text. Verification is read-only and
confined to the selected root. It checks provenance, not code semantics.

Comparison reports visible body/table/formula text, image occurrence hashes
and header/footer text. Allowed sections must be exact unique headings in both
versions; they do not implicitly permit replacing images. Figure numbers are
1-based image occurrences, not caption numbers. Changes outside the specified
scope fail; every removed text item is reported for review even inside scope.
Use `--report-only` for deliberate full rewrites; exit 0 in that mode is not a
preservation verdict. Formatting and image semantics still require page review.

`draw_sequence.py` accepts `title`, `participants` (unique `id`, `label`, `role`)
and `steps` (`from`, `to`, `text`, optional boolean `reply` and text `condition`).
It renders numbered requests, dashed replies and self calls locally. The palette
maps each declared role to explicit `fill`, `stroke` and `text` hex colors, so
existing module colors are reused across diagrams. Unknown fields, participants
or roles are rejected instead of guessed. The bounded 2–5 participants and
1–10 steps keep single-stage diagrams readable; loops/parallel stages require
an explicit custom drawing. See the authoring guide for a complete input example.

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
Rendering records the current Word SHA-256 and each generated page SHA-256;
`page_review` starts as `pending`. Rendering success is separate from actual
inspection of every final saved page. Later edits require new rendering and
page review; inspection rejects stale hashes in a successful render record.

Offline regressions with the managed runtime:

```bash
"$PYTHON" -m unittest discover -s tests -p test_docx_integrity.py -v
```
