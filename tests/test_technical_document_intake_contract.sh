#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

NAME="technical-document-intake"
SKILL="skill-packs/$NAME/SKILL.md.template"
MANIFEST="skill-packs/manifest.json"
ROUTES="mcp/workflow-gate/skill-routes.json"
TOOL="tools/document_intake.py"

if python3 - "$MANIFEST" "$ROUTES" "$NAME" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
routes = json.load(open(sys.argv[2], encoding="utf-8"))
name = sys.argv[3]
assert name in {item["name"] for item in manifest["skills"]}
route = next(item for item in routes["routes"] if item["skill"] == name)
assert {"log", "plan", "deepcheck", "audit", "verify"} <= set(route["steps"])
assert route["triggers"] and route["input_contract"] and route["output_contract"]
assert {"document_manifest", "corpus_groups", "archive_risk_register",
        "readability_gaps", "extraction_plan", "verdict"} <= set(route["output_contract"])
PY
then ok "文档/归档接入技能已发布并接入既有步骤"; else bad "文档接入 manifest/routes 合同缺失"; fi

if [[ -f "$SKILL" ]] \
  && head -4 "$SKILL" | rg -q '^description: Use when' \
  && rg -qF 'truncated_container' "$SKILL" \
  && rg -qF 'archive_risk_register' "$SKILL" \
  && rg -qF 'corpus_groups' "$SKILL" \
  && rg -qF 'requires_authorized_export' "$SKILL" \
  && rg -qF 'system alternatives' "$SKILL" \
  && rg -qF 'untrusted' "$SKILL" \
  && ! rg -q 'mcp__|TaskOutput|functions\.exec|collaboration\.' "$SKILL"; then
  ok "技能定义真实类型、结构验证、归档风险和重复/变体合同"
else
  bad "技能正文缺结构/归档/重复/安全合同"
fi

if [[ -f "tests/skill-evals/$NAME.json" ]] \
  && [[ -f "tests/skill-evals/results/$NAME-red.md" ]] \
  && [[ -f "tests/skill-evals/results/$NAME-green.md" ]]; then
  ok "文档接入技能保留 RED/GREEN eval"
else
  bad "文档接入 eval 证据缺失"
fi

TMP="$(mktemp -d -t icode-document-intake.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
CORPUS="$TMP/corpus"
OUT="$TMP/ticket"
OUTSIDE="$TMP/outside"
BAD_BIN="$TMP/bad-bin"
GOOD_BIN="$TMP/good-bin"
mkdir -p "$CORPUS/sub dir" "$OUT" "$OUTSIDE" "$BAD_BIN" "$GOOD_BIN"

printf '%%PDF-1.4\nstartxref\n0\n%%%%EOF\n' >"$CORPUS/Board_Rev1.0.pdf"
cp "$CORPUS/Board_Rev1.0.pdf" "$CORPUS/Board_Rev1.0_copy.pdf"
printf '%%PDF-1.4\nbroken\n' >"$CORPUS/invalid.pdf"
printf 'different revision\n' >"$CORPUS/Board_Rev2.0.txt"
printf '%%TSD-Header-###%% protected payload' >"$CORPUS/protected.pptx"
printf 'plain UTF-8 requirement\n' >"$CORPUS/notes.txt"
printf '\x00\x01\x02\xffopaque' >"$CORPUS/blob.bin"
: >"$CORPUS/empty.dat"
printf 'outside-secret' >"$OUTSIDE/secret.txt"
ln -s "$OUTSIDE/secret.txt" "$CORPUS/sub dir/escape-link.txt"
python3 - "$CORPUS/slides with spaces.pptx" "$CORPUS/valid.7z" "$CORPUS/truncated.7z" <<'PY'
import struct, sys, zipfile, zlib
with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr("[Content_Types].xml", "<Types/>")
    zf.writestr("ppt/presentation.xml", "<presentation/>")
fields = struct.pack("<QQI", 0, 0, 0)
header = bytes.fromhex("377abcaf271c") + bytes([0, 4]) + struct.pack("<I", zlib.crc32(fields)) + fields
open(sys.argv[2], "wb").write(header)
truncated_fields = struct.pack("<QQI", 100, 100, 0)
truncated = bytes.fromhex("377abcaf271c") + bytes([0, 4]) + struct.pack("<I", zlib.crc32(truncated_fields)) + truncated_fields
open(sys.argv[3], "wb").write(truncated)
PY

cat >"$BAD_BIN/pdfinfo" <<'SH'
#!/usr/bin/env bash
printf 'GLIBC_2.38 not found\n' >&2
exit 127
SH
cat >"$GOOD_BIN/pdfinfo" <<'SH'
#!/usr/bin/env bash
case "${1:-}" in *invalid.pdf) printf 'invalid pdf\n' >&2; exit 1;; esac
printf 'Pages: 2\nEncrypted: no\n'
SH
cat >"$GOOD_BIN/pdftotext" <<'SH'
#!/usr/bin/env bash
printf 'page one\fpage two\f'
SH
cat >"$GOOD_BIN/pdftoppm" <<'SH'
#!/usr/bin/env bash
prefix="${!#}"
printf '\x89PNG\r\n\x1a\nfixture' >"${prefix}.png"
SH
chmod +x "$BAD_BIN/pdfinfo" "$GOOD_BIN/pdfinfo" "$GOOD_BIN/pdftotext" "$GOOD_BIN/pdftoppm"

SOURCE_BEFORE="$(find "$CORPUS" -type f -print0 | sort -z | xargs -0 sha256sum)"

if [[ -f "$TOOL" ]] \
  && DOCUMENT_INTAKE_ADAPTER_PATHS="$BAD_BIN:$GOOD_BIN" \
     DOCUMENT_INTAKE_SKIP_DEFAULT_PATHS=1 \
     python3 "$TOOL" scan --root "$CORPUS" --output-root "$OUT" \
       --output "$OUT/document_manifest.json" \
       --markdown "$OUT/document_manifest.md" --probe-content >/dev/null \
  && [[ "$SOURCE_BEFORE" == "$(find "$CORPUS" -type f -print0 | sort -z | xargs -0 sha256sum)" ]] \
  && python3 - "$OUT/document_manifest.json" "$CORPUS" <<'PY'
import hashlib, json, pathlib, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
root = pathlib.Path(sys.argv[2]).resolve()
assert manifest["schema_version"] == 2
assert manifest["source_root"] == str(root)
assert manifest["security"] == {
    "document_content_trusted": False,
    "embedded_content_executed": False,
    "macros_executed": False,
    "archives_extracted": False,
    "source_files_modified": False,
    "symlinks_followed": False,
}
rows = {row["relative_path"]: row for row in manifest["files"]}
assert list(rows) == sorted(rows)
valid = rows["Board_Rev1.0.pdf"]
assert valid["detected_kind"] == "pdf" and valid["status"] == "readable"
assert valid["inspection"]["structurally_valid"] is True
assert valid["inspection"]["page_count"] == 2
assert valid["inspection"]["text_pages"] == 2
assert valid["inspection"]["renderable"] is True
attempts = valid["inspection"]["adapter_attempts"]
assert attempts[0]["tool"] == "pdfinfo" and attempts[0]["status"] == "failed"
assert any(a["tool"] == "pdfinfo" and a["status"] == "success" for a in attempts)
assert all("version" in a for a in attempts if "returncode" in a)
assert rows["invalid.pdf"]["status"] == "corrupt"
assert rows["slides with spaces.pptx"]["detected_kind"] == "ooxml_presentation"
assert rows["slides with spaces.pptx"]["inspection"]["content_probe"]["status"] == "success"
assert rows["protected.pptx"]["detected_kind"] == "protected_tsd_container"
assert rows["protected.pptx"]["status"] == "requires_authorized_export"
assert rows["valid.7z"]["detected_kind"] == "seven_zip_container"
assert rows["valid.7z"]["inspection"]["structurally_valid"] is True
assert rows["valid.7z"]["status"] in {"readable_container", "parser_required"}
assert rows["truncated.7z"]["detected_kind"] == "truncated_container"
assert rows["truncated.7z"]["status"] == "corrupt"
assert rows["blob.bin"]["status"] == "unsupported"
assert rows["sub dir/escape-link.txt"]["status"] == "skipped"
for relative, row in rows.items():
    if row["detected_kind"] != "symlink":
        data = (root / relative).read_bytes()
        assert row["sha256"] == "sha256:" + hashlib.sha256(data).hexdigest()
duplicates = manifest["corpus_groups"]["duplicate_groups"]
assert any(set(g["paths"]) == {"Board_Rev1.0.pdf", "Board_Rev1.0_copy.pdf"} for g in duplicates)
variants = manifest["corpus_groups"]["variant_groups"]
assert any(g["selection_required"] and g["canonical_path"] is None for g in variants)
archive_register = manifest["archive_risk_register"]
assert {item["relative_path"] for item in archive_register} >= {
    "slides with spaces.pptx", "valid.7z", "truncated.7z"
}
assert all(item["allowed_next_action"] for item in archive_register)
assert manifest["summary"]["requires_authorized_export"] == 1
assert "invalid.pdf" in manifest["readability_gaps"]
PY
then
  ok "工具只读验证 PDF/OOXML/TSD/7z/文本/重复变体并实际 fallback"
else
  bad "文档接入工具行为合同失败"
fi

if [[ -f "$TOOL" ]] \
  && ! python3 "$TOOL" scan --root "$CORPUS" --output-root "$OUT" \
       --output "$OUTSIDE/escape.json" --markdown "$OUT/result.md" >/dev/null 2>&1 \
  && ! python3 "$TOOL" scan --root "$CORPUS" --output-root "$OUT" \
       --output "$CORPUS/Board_Rev1.0.pdf" --markdown "$OUT/result.md" >/dev/null 2>&1; then
  ok "输出越界和覆盖输入均被拒绝"
else
  bad "输出路径保护不完整"
fi

ln -s "$OUTSIDE/symlink.json" "$OUT/symlink.json"
if [[ -f "$TOOL" ]] \
  && ! python3 "$TOOL" scan --root "$CORPUS" --output-root "$OUT" \
       --output "$OUT/symlink.json" --markdown "$OUT/result.md" >/dev/null 2>&1; then
  ok "输出拒绝符号链接"
else
  bad "输出接受符号链接"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
