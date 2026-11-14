"""Independent source fixtures for the optional, read-only fast rules view."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
STEPS = ("plan", "review", "merge", "code", "deepcheck", "audit")


def api():
    spec = importlib.util.spec_from_file_location("profile_policy", ROOT / "tools/fast_policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(data):
    text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bind(root, catalog):
    catalog["execution_profiles"]["source_sha256"] = {
        path.relative_to(root).as_posix(): digest(path.read_bytes())
        for folder in ("steps", "references") for path in (root / folder).rglob("*.md")}


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "skill"
    (root / "steps").mkdir(parents=True)
    (root / "references").mkdir()
    chunks = [f'<!-- ICODE:FAST:{step}:BEGIN -->\n<a id="fast-{step}"></a>\n'
              f'## {step.title()}\nReal duties for {step}.\n<!-- ICODE:FAST:{step}:END -->'
              for step in STEPS]
    (root / "steps/fast.md").write_bytes(("\n\n".join(chunks) + "\n").encode())
    (root / "references/shared.md").write_bytes(b"# Shared rules\nReal Read is independent.\n")
    catalog = {"execution_profiles": {"schema_version": 1,
        "hash_algorithm": "sha256_utf8_lf", "source_sha256": {}, "phases": {
            step: {"document": "steps/fast.md", "anchor": f"fast-{step}",
                   "requirements": [{"id": f"FAST-{step.upper()}-DUTIES",
                                     "source": "steps/fast.md", "anchor": f"fast-{step}"}],
                   "shared_refs": [{"source": "references/shared.md", "anchor": "shared-rules"}]}
            for step in STEPS}}}
    bind(root, catalog)
    meta = {"mode": "fast", "risk_profile": {"policy_version": 2,
        "requested_mode": "fast", "effective_mode": "fast"}}
    return root, catalog, meta


def build(fixture, step="plan"):
    root, catalog, meta = fixture
    module = api()
    assert hasattr(module, "build_execution_profile"), "missing read-only execution profile builder"
    return module.build_execution_profile(meta, step, catalog, root)


def unavailable(fixture, step="plan"):
    result = build(fixture, step)
    assert result["state"] == "unavailable"
    assert result["reason"]
    assert "rules" not in result and "source_refs" not in result


@pytest.mark.parametrize("step", STEPS)
def test_compact_returns_actual_marked_text_and_bound_reference_ids(fixture, step):
    root, catalog, _ = fixture
    result = build(fixture, step)
    document = (root / "steps/fast.md").read_text(encoding="utf-8")
    expected = document.split(f"<!-- ICODE:FAST:{step}:BEGIN -->")[1].split(
        f"<!-- ICODE:FAST:{step}:END -->")[0]
    assert result["state"] == "compact" and result["effective_mode"] == "fast"
    assert result["step"] == step and result["rules"] == expected
    assert any(ref.get("id") == f"FAST-{step.upper()}-DUTIES" for ref in result["source_refs"])
    assert {ref["source"] for ref in result["source_refs"]} == {"steps/fast.md", "references/shared.md"}
    for ref in result["source_refs"]:
        assert ref["sha256"] == catalog["execution_profiles"]["source_sha256"][ref["source"]]
        assert ref["hash_algorithm"] == "sha256_utf8_lf" and ref["anchor"]
    assert len(result["source_digest"]) == 64
    assert result["registration"] == {"preferred": "--paths-json", "max_members": 256,
        "max_json_bytes": 65536, "requires_actual_read": True, "independent_phases": True}


@pytest.mark.parametrize("location", ["mode", "requested_mode", "effective_mode"])
def test_full_dominates_without_loading_optional_catalog(fixture, location):
    root, _, meta = fixture
    (meta if location == "mode" else meta["risk_profile"])[location] = "full"
    fixture = root, None, meta
    result = build(fixture)
    assert result["state"] == "standard" and result["effective_mode"] == "full"
    assert "rules" not in result


@pytest.mark.parametrize("version", ["absent", 1])
def test_legacy_fast_remains_standard(fixture, version):
    root, _, meta = fixture
    if version == "absent":
        meta.pop("risk_profile")
    else:
        meta["risk_profile"]["policy_version"] = version
    assert build((root, None, meta))["state"] == "standard"


@pytest.mark.parametrize("version", [True, "2", 0, 3, None])
def test_unsupported_versions_never_compact(fixture, version):
    fixture[2]["risk_profile"]["policy_version"] = version
    unavailable(fixture)


@pytest.mark.parametrize("step", ["patch", "../plan", "01_plan", None, [], "x" * 1000])
def test_unsupported_steps_are_bounded(fixture, step):
    unavailable(fixture, step)
    assert len(json.dumps(build(fixture, step))) < 1000


@pytest.mark.parametrize("meta", [None, [], "fast", {"mode": []}, {"risk_profile": []}])
def test_invalid_metadata_is_unavailable(fixture, meta):
    unavailable((fixture[0], fixture[1], meta))


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(schema_version=True),
    lambda p: p.update(schema_version=2),
    lambda p: p.update(hash_algorithm="sha256"),
    lambda p: p.update(source_sha256=[]),
    lambda p: p["source_sha256"].pop("references/shared.md"),
    lambda p: p["source_sha256"].update({"steps/fast.md": "0" * 64}),
    lambda p: p["source_sha256"].update({"references/unbound.md": "0" * 64}),
    lambda p: p.update(phases=[]),
    lambda p: p["phases"].pop("audit"),
    lambda p: p["phases"]["plan"].update(requirements=[]),
    lambda p: p["phases"]["plan"].update(shared_refs="references/shared.md"),
    lambda p: p["phases"]["plan"].update(anchor="fast-code"),
    lambda p: p["phases"]["plan"]["requirements"][0].update(id=[]),
    lambda p: p["phases"]["plan"]["requirements"][0].update(anchor="absent"),
    lambda p: p["phases"]["plan"]["shared_refs"][0].update(anchor="absent"),
    lambda p: p["phases"]["plan"]["requirements"].append(
        dict(p["phases"]["plan"]["requirements"][0])),
    lambda p: p["phases"]["plan"].update(requirements=[{}] * 257),
    lambda p: p["phases"]["plan"].update(shared_refs=[{}] * 257),
])
def test_invalid_catalog_never_yields_compact(fixture, mutation):
    mutation(fixture[1]["execution_profiles"])
    unavailable(fixture)


@pytest.mark.parametrize("catalog", [None, [], {}, {"execution_profiles": None}])
def test_absent_or_wrongly_typed_catalog(fixture, catalog):
    unavailable((fixture[0], catalog, fixture[2]))


@pytest.mark.parametrize("path", ["../escape.md", "steps/../references/shared.md", "steps//fast.md",
    "steps/./fast.md", "steps\\fast.md", "/steps/fast.md", "C:/escape.md", "other/file.md",
    "steps/fast.txt", "steps/fast.md:stream", "steps/fast.md\x00"])
def test_unsafe_or_noncanonical_markdown_paths_rejected(fixture, path):
    fixture[1]["execution_profiles"]["phases"]["plan"]["shared_refs"][0]["source"] = path
    fixture[1]["execution_profiles"]["source_sha256"][path] = "0" * 64
    unavailable(fixture)


@pytest.mark.parametrize("mutation", [
    lambda s: s.replace("<!-- ICODE:FAST:plan:BEGIN -->", ""),
    lambda s: s.replace("<!-- ICODE:FAST:plan:END -->", ""),
    lambda s: s + "\n<!-- ICODE:FAST:plan:BEGIN -->",
    lambda s: s + "\n<!-- ICODE:FAST:plan:END -->",
    lambda s: s.replace('<a id="fast-plan"></a>', '') + '\n<a id="fast-plan"></a>',
    lambda s: s.replace('<a id="fast-plan"></a>', '<a id="fast-plan"></a>' * 2),
    lambda s: s.replace("Real duties for plan.", ""),
    lambda s: s.replace("Real duties for audit.", "Z" * 65537),
])
def test_missing_duplicate_misplaced_or_oversized_phase_sources_rejected(fixture, mutation):
    root, catalog, _ = fixture
    path = root / "steps/fast.md"
    path.write_bytes(mutation(path.read_text(encoding="utf-8")).encode())
    bind(root, catalog)
    unavailable(fixture)


def test_stale_version_in_another_phase_binding_is_rejected(fixture):
    root, catalog, _ = fixture
    (root / "steps/audit.md").write_bytes(b"# Audit proof\nIndependent Read\n")
    catalog["execution_profiles"]["phases"]["audit"]["requirements"][0].update(
        source="steps/audit.md", anchor="audit-proof")
    bind(root, catalog)
    (root / "steps/audit.md").write_bytes(b"# Audit proof\nChanged\n")
    unavailable(fixture)


def test_crlf_hash_binding_preserves_exact_phase_text(fixture):
    root, _, _ = fixture
    first = build(fixture)
    for path in (root / "steps/fast.md", root / "references/shared.md"):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    second = build(fixture)
    assert second["state"] == "compact"
    assert second["source_digest"] == first["source_digest"]
    assert second["rules"] == first["rules"].replace("\n", "\r\n")


def test_no_mutation_or_persisted_artifact_and_no_cross_call_cache(fixture):
    root, catalog, meta = fixture
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    inputs = copy.deepcopy((meta, catalog))
    assert build(fixture)["state"] == "compact"
    assert (meta, catalog) == inputs
    assert before == {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    (root / "references/shared.md").write_bytes(b"# Shared rules\nDrift\n")
    unavailable(fixture)


@pytest.mark.parametrize("linked_part", ["file", "parent"])
def test_symlink_escape_rejected(fixture, tmp_path, linked_part):
    root, catalog, _ = fixture
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "shared.md").write_bytes(b"# Shared rules\nReal Read is independent.\n")
    link = root / ("references/linked.md" if linked_part == "file" else "references/linked")
    try:
        link.symlink_to(outside / "shared.md" if linked_part == "file" else outside,
                        target_is_directory=linked_part == "parent")
    except OSError:
        pytest.skip("symlink creation unavailable to this account")
    source = "references/linked.md" if linked_part == "file" else "references/linked/shared.md"
    catalog["execution_profiles"]["phases"]["plan"]["shared_refs"][0]["source"] = source
    catalog["execution_profiles"]["source_sha256"][source] = digest((outside / "shared.md").read_bytes())
    unavailable(fixture)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior")
def test_junction_parent_escape_rejected(fixture, tmp_path):
    root, catalog, _ = fixture
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "shared.md").write_bytes(b"# Shared rules\nRead\n")
    link = root / "references/linked"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    source = "references/linked/shared.md"
    catalog["execution_profiles"]["phases"]["plan"]["shared_refs"][0]["source"] = source
    catalog["execution_profiles"]["source_sha256"][source] = digest((outside / "shared.md").read_bytes())
    unavailable(fixture)


@pytest.mark.parametrize("content", [b"\xff", b"X" * (1024 * 1024 + 1)], ids=["invalid-utf8", "oversized"])
def test_invalid_utf8_and_source_size_budget(fixture, content):
    root, catalog, _ = fixture
    (root / "references/shared.md").write_bytes(content)
    if content != b"\xff":
        bind(root, catalog)
    unavailable(fixture)


@pytest.mark.parametrize("content", [
    '# Shared rules\n# Shared rules\nDuplicate heading IDs\n',
    '```html\n<a id="shared-rules"></a>\n```\nFake code anchor\n',
    '```markdown\n# Shared rules\n```\nFake code heading\n',
])
def test_duplicate_or_fenced_reference_anchors_are_rejected(fixture, content):
    root, catalog, _ = fixture
    (root / "references/shared.md").write_bytes(content.encode())
    bind(root, catalog)
    unavailable(fixture)


@pytest.mark.parametrize("field,value", [("requirements", [None]), ("shared_refs", [None]),
    ("document", []), ("anchor", []), ("requirements", [{"id": "X", "source": [], "anchor": "x"}])])
def test_wrongly_typed_phase_fields_are_unavailable(fixture, field, value):
    fixture[1]["execution_profiles"]["phases"]["plan"][field] = value
    unavailable(fixture)


@pytest.mark.parametrize("bad_root", [None, 42, "absent-directory"])
def test_invalid_skill_root_is_unavailable(fixture, bad_root):
    unavailable((bad_root, fixture[1], fixture[2]))


def test_total_source_budget_and_document_count_are_bounded(fixture):
    root, catalog, _ = fixture
    for index in range(5):
        (root / f"references/large{index}.md").write_bytes(b"X" * (1024 * 1024))
    bind(root, catalog)
    unavailable(fixture)
    catalog["execution_profiles"]["source_sha256"] = {f"references/doc{i}.md": "0" * 64 for i in range(65)}
    unavailable(fixture)


@pytest.mark.parametrize("source", ["references/new\nline.md", "references/tab\tname.md"])
def test_control_characters_are_not_canonical_paths(fixture, source):
    root, catalog, _ = fixture
    # Linux allows these filenames. Reject them before opening on all hosts.
    if os.name != "nt":
        (root / source).write_bytes(b"# Shared rules\nBad canonical path\n")
        bind(root, catalog)
    catalog["execution_profiles"]["source_sha256"][source] = digest(b"# Shared rules\nBad canonical path\n")
    catalog["execution_profiles"]["phases"]["plan"]["shared_refs"][0]["source"] = source
    unavailable(fixture)
    with pytest.raises(ValueError, match="invalid_source_path"):
        api()._profile_document(root, source, 1024 * 1024)
