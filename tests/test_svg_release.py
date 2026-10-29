"""Real file/ZIP/hash preflight tests; no simulated Office success claims."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "tools/docx/check_svg_release.py"


def api():
    assert MODULE.is_file(), "the reusable release consumer checker must exist"
    sys.path.insert(0, str(MODULE.parent))
    spec = importlib.util.spec_from_file_location("check_svg_release", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def fixture_tree(tmp_path):
    """Small real ZIP packages exercise integrity gates, never Office rendering."""
    directory = tmp_path / "fixtures"
    directory.mkdir()
    diagrams = []
    for index in range(3):
        source = directory / f"diagram-{index}.svg"
        source.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle r="{index + 1}"/></svg>', encoding="utf-8")
        fallback = directory / f"fallback-{index}.png"
        fallback.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([index]))
        diagrams.append({"id": str(index), "source": str(source), "sha256": digest(source), "fallback_png": str(fallback)})
    packages = []
    for mode in ("raster", "dual"):
        for extension, prefix in (("docx", "word"), ("pptx", "ppt")):
            package = directory / f"qualification-{mode}.{extension}"
            with zipfile.ZipFile(package, "w") as archive:
                for index, diagram in enumerate(diagrams):
                    archive.write(diagram["fallback_png"], f"{prefix}/media/image{index}.png")
                    if mode == "dual":
                        archive.write(diagram["source"], f"{prefix}/media/image{index}.svg")
            manifest = package.with_suffix(package.suffix + ".manifest.json")
            records = [{**diagram, "source_format": "svg", "fallback_sha256": digest(Path(diagram["fallback_png"])),
                        "renderer": {"name": "resvg-py", "version": "0.5.0"}} for diagram in diagrams]
            save_json(manifest, {"schema_version": 1, "output": {"path": str(package), "sha256": digest(package)},
                                 "source_images" if extension == "docx" else "images": records})
            packages.append({"path": str(package), "mode": mode, "manifest": str(manifest)})
    data = {"schema_version": 1, "status": {"build": "passed"}, "output_dir": str(directory),
            "diagrams": diagrams, "packages": packages}
    rehash(directory, data)
    return directory, data


def rehash(directory, data):
    data["artifacts"] = [{"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}
                         for path in sorted(directory.rglob("*")) if path.is_file() and path.name != "qualification.json"]
    save_json(directory / "qualification.json", data)


def test_good_input_preflight_checks_four_hash_bound_packages(tmp_path):
    fixtures, _ = fixture_tree(tmp_path)
    result = api()._validate_inputs(fixtures)
    assert len(result["packages"]) == 4
    assert len(result["source_sha256"]) == 3


@pytest.mark.parametrize("mutation", ["tampered", "missing", "missing-hash", "unlisted", "three-packages", "duplicate-package", "bad-manifest-hash", "bad-svg-version", "bad-source-hash", "self-hash"])
def test_input_failures_block_resolver_and_renderer(tmp_path, monkeypatch, mutation):
    fixtures, data = fixture_tree(tmp_path)
    first = Path(data["packages"][0]["path"])
    if mutation == "tampered":
        first.write_bytes(first.read_bytes() + b"changed")
    elif mutation == "missing":
        first.unlink()
    elif mutation == "missing-hash":
        data["artifacts"][0].pop("sha256")
    elif mutation == "unlisted":
        (fixtures / "extra.txt").write_text("not listed")
    elif mutation == "three-packages":
        data["packages"].pop()
    elif mutation == "duplicate-package":
        data["packages"][1] = data["packages"][0]
    elif mutation in ("bad-manifest-hash", "bad-svg-version"):
        path = Path(data["packages"][0]["manifest"])
        manifest = json.loads(path.read_text())
        if mutation == "bad-manifest-hash":
            manifest["output"]["sha256"] = "0" * 64
        else:
            manifest["source_images"][0]["renderer"]["version"] = "wrong"
        save_json(path, manifest)
        rehash(fixtures, data)
    elif mutation == "bad-source-hash":
        data["diagrams"][0]["sha256"] = "0" * 64
    elif mutation == "self-hash":
        data["artifacts"].append({"path": str(fixtures / "qualification.json"), "sha256": "0" * 64, "bytes": 1})
    save_json(fixtures / "qualification.json", data)
    module = api()
    monkeypatch.setattr(module, "resolve", lambda: pytest.fail("invalid inputs must not reach resolver"))
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("invalid inputs must not execute renderer"))
    output = tmp_path / "results"
    with pytest.raises(ValueError):
        module.check_release(fixtures, output)
    assert not output.exists()


def test_missing_owned_renderer_fails_closed_without_creating_output(tmp_path, monkeypatch):
    fixtures, _ = fixture_tree(tmp_path)
    module = api()
    monkeypatch.setattr(module, "resolve", lambda: {"status": "unavailable", "reason": "fixture missing renderer"})
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("must not fall back to PATH"))
    with pytest.raises(RuntimeError, match="managed renderer"):
        module.check_release(fixtures, tmp_path / "results")
    assert not (tmp_path / "results").exists()


def test_wrong_owned_renderer_version_never_executes(tmp_path, monkeypatch):
    fixtures, _ = fixture_tree(tmp_path)
    module = api()
    monkeypatch.setattr(module, "resolve", lambda: {"status": "ready", "renderer": {"id": "old-version"}})
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("wrong version must not run"))
    with pytest.raises(RuntimeError, match="version"):
        module.check_release(fixtures, tmp_path / "results")


@pytest.mark.parametrize("location", ["existing", "inside-fixtures", "inside-repository"])
def test_output_must_be_new_and_external(tmp_path, monkeypatch, location):
    fixtures, _ = fixture_tree(tmp_path)
    output = tmp_path / "existing" if location == "existing" else fixtures / "new" if location == "inside-fixtures" else ROOT / "must-not-create-release-output"
    if location == "existing":
        output.mkdir()
    module = api()
    monkeypatch.setattr(module, "resolve", lambda: pytest.fail("invalid output must be rejected first"))
    with pytest.raises(ValueError):
        module.check_release(fixtures, output)


def test_roundtrip_rejects_lost_or_rewritten_svg_even_when_png_exists(tmp_path):
    fixtures, data = fixture_tree(tmp_path)
    module = api()
    package = next(Path(p["path"]) for p in data["packages"] if p["mode"] == "dual")
    original = module._media(package, "dual", {d["sha256"] for d in data["diagrams"]})
    assert original["svg_count"] == 3 and original["png_count"] == 3
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("word/media/retained.png", b"PNG fallback still present")
        for index in range(3):
            archive.writestr(f"word/media/replaced{index}.svg", f"changed-{index}")
    with pytest.raises(ValueError, match="SVG"):
        module._media(package, "dual", {d["sha256"] for d in data["diagrams"]})


PDFIMAGES_HEADER = 'page num type width height color comp bpc enc interp object ID x-ppi y-ppi size ratio\n' + '-' * 92 + '\n'
PDFIMAGES_ROWS = ''.join(f'{page} {page - 1} image 1200 583 rgb 3 8 image no {page * 10} 0 192 192 31K 1.6%\n' for page in range(1, 4))


def test_pdf_image_gate_accepts_vector_zero_and_raster_three():
    module = api()
    assert module._image_objects(PDFIMAGES_HEADER, 'dual') == []
    images = module._image_objects(PDFIMAGES_HEADER + PDFIMAGES_ROWS, 'raster')
    assert len(images) == 3
    assert [item['page'] for item in images] == [1, 2, 3]


@pytest.mark.parametrize('text,mode', [
    ('', 'dual'), ('no images found', 'dual'),
    (PDFIMAGES_HEADER + 'unexpected diagnostic\n', 'dual'),
    (PDFIMAGES_HEADER + '1 0 image invalid row\n', 'raster'),
    (PDFIMAGES_HEADER + PDFIMAGES_ROWS, 'dual'),
    (PDFIMAGES_HEADER, 'raster'),
])
def test_pdf_image_gate_rejects_ambiguous_output_and_wrong_representation(text, mode):
    with pytest.raises(ValueError):
        api()._image_objects(text, mode)


def pdfimages_bundle(tmp_path):
    bundle = tmp_path / 'owned-bundle'
    executable = bundle / 'root/usr/bin/pdfimages'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'owned executable fixture; never executed')
    executable.chmod(0o755)
    library = bundle / 'root/usr/lib/owned'
    library.mkdir(parents=True)
    metadata = {'payload_sha256': {'root/usr/bin/pdfimages': digest(executable)}, 'library_dir': 'root/usr/lib/owned'}
    save_json(bundle / 'payload_manifest.json', metadata)
    return {'id': 'owned-fixture', 'bundle': str(bundle)}, executable, metadata


def test_owned_pdfimages_is_hash_bound_with_owned_libraries(tmp_path):
    renderer, executable, _ = pdfimages_bundle(tmp_path)
    tool = api()._pdfimages(renderer)
    assert tool['path'] == str(executable)
    assert tool['sha256'] == digest(executable)
    assert Path(tool['library_dir']).is_relative_to(Path(renderer['bundle']))


@pytest.mark.parametrize('mutation', ['missing-hash', 'tampered', 'missing-executable', 'library-escape'])
def test_pdfimages_missing_or_tampered_fails_before_native_execution(tmp_path, monkeypatch, mutation):
    renderer, executable, metadata = pdfimages_bundle(tmp_path)
    if mutation == 'missing-hash':
        metadata['payload_sha256'].clear()
    elif mutation == 'tampered':
        executable.write_bytes(b'tampered binary')
    elif mutation == 'missing-executable':
        executable.unlink()
    elif mutation == 'library-escape':
        metadata['library_dir'] = '../outside'
    save_json(Path(renderer['bundle']) / 'payload_manifest.json', metadata)
    module = api()
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: pytest.fail('unverified pdfimages must never execute'))
    with pytest.raises((ValueError, RuntimeError)):
        module._pdfimages(renderer)
