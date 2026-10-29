#!/usr/bin/env python3
"""Check SVG fixtures with the current owned LibreOffice/Poppler bundle.

Checksums bind all inputs before any consumer process runs. Each of four packages
must export three pages, survive a same-format save, then export three pages
again. Dual packages must retain the three original SVG byte streams and PNG
fallbacks. PDF image-object inspection must also confirm dual output has zero
bitmap objects and raster output has at least three. This records technical
rendering/roundtrip/vector evidence, never visual approval.
No installation, PATH Office lookup, or build-manifest modification is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

from bootstrap_renderer import CATALOG, catalog_entries, package_key
from bootstrap_runtime import locked_versions
from resolve_renderer import resolve
from renderer_support import owned_path


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _file(path: Path) -> dict:
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
        raise ValueError('missing/empty/linked artifact: ' + str(path))
    return {'path': str(path), 'sha256': _sha(path), 'bytes': path.stat().st_size}


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('JSON object required: ' + str(path))
    return value


def _bound(raw: str, root: Path) -> Path:
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('artifact path must be an absolute owned fixture path: ' + str(raw))
    return path.resolve()


def _media(package: Path, mode: str, expected: set[str]) -> dict:
    result = {'svg': [], 'png': []}
    prefix = 'word/media/' if package.suffix == '.docx' else 'ppt/media/'
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names):
            raise ValueError('duplicate ZIP parts: ' + str(package))
        total = 0
        for info in archive.infolist():
            kind = 'svg' if info.filename.lower().endswith('.svg') else 'png' if info.filename.lower().endswith('.png') else None
            if kind is None or not info.filename.startswith(prefix):
                continue
            total += info.file_size
            if info.file_size > 128 * 1024 * 1024 or total > 256 * 1024 * 1024:
                raise ValueError('excessive package media size')
            result[kind].append({'part': info.filename, 'sha256': hashlib.sha256(archive.read(info)).hexdigest(), 'bytes': info.file_size})
    hashes = {part['sha256'] for part in result['svg']}
    if mode == 'dual' and (len(result['svg']) != 3 or hashes != expected):
        raise ValueError('dual package must retain exactly three original SVG source hashes: ' + str(package))
    if mode == 'raster' and result['svg']:
        raise ValueError('raster package unexpectedly contains SVG media')
    if len(result['png']) < 3:
        raise ValueError('three PNG fallbacks are required: ' + str(package))
    return {**result, 'svg_count': len(result['svg']), 'png_count': len(result['png'])}


def _inputs(fixtures: Path) -> dict:
    qualification = fixtures / 'qualification.json'
    data = _json(qualification)
    if data.get('schema_version') != 1 or data.get('status', {}).get('build') != 'passed' or Path(data['output_dir']).resolve() != fixtures:
        raise ValueError('invalid qualification schema/build/output directory')
    artifacts = {}
    for record in data['artifacts']:
        path = _bound(record['path'], fixtures)
        if path == qualification or path in artifacts or not re.fullmatch(r'[0-9a-f]{64}', record['sha256']):
            raise ValueError('duplicate/self/missing artifact hash')
        if type(record['bytes']) is not int or record['bytes'] <= 0 or _file(path) != record:
            raise ValueError('artifact size/SHA-256 mismatch: ' + str(path))
        artifacts[path] = record
    tree = list(fixtures.rglob('*'))
    if any(path.is_symlink() for path in tree):
        raise ValueError('fixture symlinks are not allowed')
    if {path.resolve() for path in tree if path.is_file() and path != qualification} != set(artifacts):
        raise ValueError('fixture file inventory differs from qualification artifacts')
    def listed(raw):
        path = _bound(raw, fixtures)
        if path not in artifacts:
            raise ValueError('required file is not hash bound: ' + str(path))
        return path
    diagrams = data['diagrams']
    if len(diagrams) != 3 or len({item['id'] for item in diagrams}) != 3:
        raise ValueError('exactly three distinct diagrams required')
    sources = set()
    for diagram in diagrams:
        source = listed(diagram['source'])
        fallback = listed(diagram['fallback_png'])
        if source.suffix != '.svg' or fallback.suffix != '.png' or artifacts[source]['sha256'] != diagram['sha256']:
            raise ValueError('diagram source/fallback hash mismatch')
        sources.add(diagram['sha256'])
    if len(sources) != 3:
        raise ValueError('three distinct SVG source hashes required')
    packages, combinations = [], set()
    if len(data['packages']) != 4:
        raise ValueError('exactly four packages required')
    for item in data['packages']:
        package, manifest_path = listed(item['path']), listed(item['manifest'])
        combination = (item['mode'], package.suffix)
        if combination in combinations or combination not in {(m, e) for m in ('raster', 'dual') for e in ('.docx', '.pptx')}:
            raise ValueError('duplicate or unsupported package mode/format')
        combinations.add(combination)
        manifest = _json(manifest_path)
        if manifest.get('schema_version') != 1 or listed(manifest['output']['path']) != package or manifest['output']['sha256'] != artifacts[package]['sha256']:
            raise ValueError('package build manifest output hash mismatch')
        images = manifest['source_images' if package.suffix == '.docx' else 'images']
        if len(images) != 3 or {image['sha256'] for image in images} != sources:
            raise ValueError('package manifest must bind all three original SVG sources')
        for image in images:
            if artifacts[listed(image['source'])]['sha256'] != image['sha256'] or artifacts[listed(image['fallback_png'])]['sha256'] != image['fallback_sha256']:
                raise ValueError('package source/fallback hash mismatch')
            if image['renderer'].get('name') != 'resvg-py' or image['renderer'].get('version') != locked_versions()['resvg_py']:
                raise ValueError('SVG renderer version differs from current runtime lock')
        packages.append({'input': artifacts[package], 'mode': item['mode'], 'manifest': artifacts[manifest_path],
                         'media': _media(package, item['mode'], sources)})
    return {'qualification': _file(qualification), 'artifacts': list(artifacts.values()), 'packages': packages,
            'source_sha256': sorted(sources)}


def _validate_inputs(fixtures: Path) -> dict:
    try:
        return _inputs(fixtures)
    except (KeyError, TypeError, AttributeError, OSError, zipfile.BadZipFile) as exc:
        raise ValueError('invalid/incomplete qualification inputs: ' + str(exc)) from exc


def _owned_renderer() -> dict:
    resolved = resolve()
    if resolved.get('status') != 'ready':
        raise RuntimeError('current managed renderer is unavailable; no PATH fallback: ' + json.dumps(resolved))
    renderer = resolved['renderer']
    identities = {package['id'] + '-' + package_key(package)[:16] for package in catalog_entries(CATALOG)}
    if renderer.get('id') not in identities:
        raise RuntimeError('managed renderer version/launcher differs from current package lock')
    bundle = Path(renderer['bundle'])
    for name in ('soffice', 'pdftoppm'):
        executable = Path(renderer[name])
        if not executable.is_absolute() or not executable.resolve().is_relative_to(bundle.resolve()) or not executable.is_file():
            raise RuntimeError('managed renderer executable escaped its verified bundle')
    _pdfimages(renderer)
    return renderer


def _pdfimages(renderer: dict) -> dict:
    """Resolve the additional Poppler tool only from the hash-verified bundle."""
    try:
        bundle = Path(renderer['bundle']).resolve()
        manifest = owned_path(bundle, 'payload_manifest.json')
        metadata = _json(manifest)
        executable = owned_path(bundle, 'root/usr/bin/pdfimages')
        library = owned_path(bundle, metadata['library_dir'])
        expected = metadata['payload_sha256'].get(executable.relative_to(bundle).as_posix(), '')
        actual = _file(executable)
        if not re.fullmatch(r'[0-9a-f]{64}', expected) or actual['sha256'] != expected or not os.access(executable, os.X_OK):
            raise ValueError('owned pdfimages is missing a matching executable SHA-256')
        if not library.is_dir():
            raise ValueError('owned pdfimages library directory is missing')
        return {**actual, 'library_dir': str(library), 'payload_manifest': _file(manifest)}
    except (OSError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError('invalid owned pdfimages payload: ' + str(exc)) from exc


def _image_objects(text: str, mode: str) -> list[dict]:
    """Parse this locked Poppler table strictly; malformed output never means 0."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    header = 'page num type width height color comp bpc enc interp object ID x-ppi y-ppi size ratio'.split()
    if len(lines) < 2 or lines[0].split() != header or len(lines[1]) < 10 or set(lines[1]) != {'-'}:
        raise ValueError('unrecognized pdfimages -list output')
    objects = []
    for line in lines[2:]:
        fields = line.split()
        if (len(fields) != 16 or fields[2] not in ('image', 'mask', 'smask', 'stencil')
                or any(not fields[index].isdigit() for index in (0, 1, 3, 4, 6, 7, 10, 11))
                or int(fields[0]) not in (1, 2, 3) or int(fields[3]) <= 0 or int(fields[4]) <= 0):
            raise ValueError('unrecognized PDF image-object row')
        objects.append({'page': int(fields[0]), 'number': int(fields[1]), 'type': fields[2],
                        'width': int(fields[3]), 'height': int(fields[4]), 'object_id': [int(fields[10]), int(fields[11])]})
    if mode not in ('dual', 'raster') or (mode == 'dual' and objects) or (mode == 'raster' and len(objects) < 3):
        raise ValueError('PDF image-object gate failed: mode=' + str(mode) + ', image_objects=' + str(len(objects)))
    return objects


def _pdf_vector(pdf: Path, mode: str, renderer: dict, executions: list) -> dict:
    """Check one existing PDF; reusable for an already rendered acceptance set."""
    tool = _pdfimages(renderer)
    environment = os.environ.copy()
    environment.update(LD_LIBRARY_PATH=tool['library_dir'], LC_ALL='C')
    record = _run([tool['path'], '-list', str(pdf)], pdf, [], renderer, executions, env=environment)
    objects = _image_objects(record['stdout'], mode)
    if _file(Path(tool['path']))['sha256'] != tool['sha256']:
        raise RuntimeError('owned pdfimages changed during inspection')
    return {'status': 'passed', 'pdf': record['input'], 'mode': mode, 'image_objects': len(objects),
            'objects': objects, 'tool': tool, 'execution': record, 'visual_review': 'pending'}


def _run(argv: list[str], source: Path, outputs: list[Path], renderer: dict, executions: list, *, env: dict | None = None) -> dict:
    record = {'command': argv, 'renderer_id': renderer['id'], 'input': _file(source), 'code': None, 'outputs': []}
    executions.append(record)
    if env is not None:
        record['environment'] = {key: env[key] for key in ('LD_LIBRARY_PATH', 'LC_ALL') if key in env}
    try:
        process = subprocess.run(argv, capture_output=True, text=True, timeout=300, env=env)
        record.update(code=process.returncode, stdout=process.stdout, stderr=process.stderr)
        if process.returncode:
            raise RuntimeError('consumer command failed with code ' + str(process.returncode))
        record['outputs'] = [_file(path) for path in outputs]
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        record['error'] = str(exc)
        raise RuntimeError('consumer command/output failed: ' + str(exc)) from exc
    if _file(source) != record['input']:
        raise RuntimeError('consumer modified its input: ' + str(source))
    return record


def _convert(source: Path, kind: str, directory: Path, renderer: dict, executions: list) -> Path:
    directory.mkdir()
    target = directory / (source.stem + '.' + kind)
    _run([renderer['soffice'], '--headless', '--norestore', '--nologo', '--nofirststartwizard',
          '--convert-to', kind, '--outdir', str(directory), str(source)], source, [target], renderer, executions)
    if kind == 'pdf' and target.read_bytes()[:5] != b'%PDF-':
        raise ValueError('consumer did not produce a PDF')
    return target


def _pages(pdf: Path, directory: Path, renderer: dict, executions: list) -> list[dict]:
    directory.mkdir()
    record = _run([renderer['pdftoppm'], '-png', '-r', '96', str(pdf), str(directory / 'page')],
                  pdf, [], renderer, executions)
    pages = sorted(directory.glob('page-*.png'))
    if len(pages) != 3 or {int(path.stem.split('-')[-1]) for path in pages} != {1, 2, 3}:
        raise ValueError('consumer export must contain exactly three pages: ' + str(pdf))
    for path in pages:
        with path.open('rb') as stream:
            if stream.read(8) != b'\x89PNG\r\n\x1a\n':
                raise ValueError('consumer page is not PNG')
    record['outputs'] = [_file(path) for path in pages]
    return record['outputs']


def _write(output: Path, report: dict) -> None:
    temporary = output / '.release-check.tmp'
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(output / 'release-check.json')


def check_release(fixtures: Path, output: Path) -> dict:
    """Run the four-package open-source consumer gate in a new external folder."""
    raw_output = Path(output).expanduser()
    if raw_output.exists() or raw_output.is_symlink():
        raise ValueError('output directory must be new; refusing to overwrite')
    fixtures, output = Path(fixtures).expanduser().resolve(), raw_output.resolve()
    repository = next((p for p in Path(__file__).resolve().parents if (p / '.git').exists()), None)
    if output.is_relative_to(fixtures) or (repository and output.is_relative_to(repository)):
        raise ValueError('output must be outside fixtures and repository')
    checked = _validate_inputs(fixtures)
    renderer = _owned_renderer()
    output.mkdir(parents=True)
    report = {'schema_version': 1, 'qualification': checked['qualification'], 'renderer': renderer,
              'source_sha256': checked['source_sha256'], 'packages': [],
              'status': {'input_validation': 'passed', 'render': 'pending', 'roundtrip': 'pending', 'pdf_vector': 'pending', 'visual_review': 'pending'},
              'scope': 'Managed LibreOffice/Poppler rendering and roundtrip evidence only; visual review pending. Microsoft Office/WPS optional.'}
    try:
        for package in checked['packages']:
            source = Path(package['input']['path'])
            directory = output / (source.stem + '-' + source.suffix[1:])
            directory.mkdir()
            record = {**package, 'renderer_id': renderer['id'], 'executions': [], 'visual_review': 'pending'}
            report['packages'].append(record)
            pdf = _convert(source, 'pdf', directory / 'pdf', renderer, record['executions'])
            record['pdf'], record['pages'] = _file(pdf), _pages(pdf, directory / 'pages', renderer, record['executions'])
            record['pdf_vector'] = _pdf_vector(pdf, package['mode'], renderer, record['executions'])
            resaved = _convert(source, source.suffix[1:], directory / 'roundtrip', renderer, record['executions'])
            record['resaved'], record['resaved_media'] = _file(resaved), _media(resaved, package['mode'], set(checked['source_sha256']))
            resaved_pdf = _convert(resaved, 'pdf', directory / 'roundtrip-pdf', renderer, record['executions'])
            record['roundtrip_pdf'] = _file(resaved_pdf)
            record['roundtrip_pages'] = _pages(resaved_pdf, directory / 'roundtrip-pages', renderer, record['executions'])
            record['roundtrip_pdf_vector'] = _pdf_vector(resaved_pdf, package['mode'], renderer, record['executions'])
            record['status'] = {'render': 'passed', 'roundtrip': 'passed', 'pdf_vector': 'passed', 'visual_review': 'pending'}
            _write(output, report)
        if _file(fixtures / 'qualification.json') != checked['qualification'] or any(_file(Path(item['path'])) != item for item in checked['artifacts']):
            raise RuntimeError('qualification inputs changed during consumer checks')
        report['status'].update(render='passed', roundtrip='passed', pdf_vector='passed')
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        report['status'].update(render='failed', roundtrip='failed', pdf_vector='failed')
        report['error'] = str(exc)
        _write(output, report)
        raise RuntimeError('SVG release check failed; evidence: ' + str(output / 'release-check.json') + ': ' + str(exc)) from exc
    _write(output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = check_release(args.fixtures, args.output_dir)
        print(json.dumps({'result': str(args.output_dir.resolve() / 'release-check.json'), 'status': report['status']}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print('ERROR: SVG release check failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
