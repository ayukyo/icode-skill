#!/usr/bin/env python3
"""Install a pinned owned renderer outside skill trees; never use system Office."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from renderer_support import TOOL_DIR, host, matches, owned_path, runtime_root, sha256_file, validate_entry

CATALOG = TOOL_DIR / 'renderer_packages.lock.json'
MAX_EXTRACT_BYTES = 4 * 1024 ** 3
INSTALLER_VERSION = '1.0.0'


def atomic_json(path: Path, data: dict) -> None:
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.renderer-', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def catalog_entries(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not isinstance(data.get('packages'), list):
        raise ValueError('invalid renderer package catalog')
    for entry in data['packages']:
        if not re.fullmatch(r'[a-zA-Z0-9._-]+', entry['id']) or entry['id'] in ('.', '..'):
            raise ValueError('invalid renderer package id')
        if not entry.get('artifacts'):
            raise ValueError('renderer package has no artifacts')
        for artifact in entry['artifacts']:
            if (Path(artifact['name']).name != artifact['name'] or artifact['name'] in ('.', '..')
                    or not re.fullmatch(r'[0-9a-f]{64}', artifact['sha256'])
                    or not isinstance(artifact['bytes'], int) or artifact['bytes'] <= 0
                    or artifact['kind'] not in ('deb', 'libreoffice-debs') or not artifact['urls']):
                raise ValueError('invalid renderer artifact')
            for url in artifact['urls']:
                parsed = urllib.parse.urlsplit(url)
                if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('renderer downloads require credential-free HTTPS')
        for relative in [*entry['executables'].values(), entry['library_dir']]:
            owned_path(Path('/catalog-check'), relative)
        if set(entry['executables']) != {'soffice', 'pdftoppm'}:
            raise ValueError('renderer must provide soffice and pdftoppm')
    return data['packages']


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('renderer download redirected away from HTTPS')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def verify_artifact(path: Path, artifact: dict) -> None:
    if path.stat().st_size != artifact['bytes'] or sha256_file(path) != artifact['sha256']:
        raise ValueError('renderer artifact size/SHA-256 mismatch: ' + artifact['name'])


def download(artifact: dict, cache: Path, offline: bool) -> Path:
    target = cache / artifact['name']
    if target.exists():
        verify_artifact(target, artifact)
        return target
    if offline:
        raise ValueError('offline renderer artifact missing: ' + str(target))
    cache.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(HTTPSRedirect())
    for index, url in enumerate(artifact['urls']):
        print('INFO: renderer download ' + artifact['name'] + ' source ' + str(index + 1), file=sys.stderr, flush=True)
        with tempfile.NamedTemporaryFile(dir=cache, prefix='.renderer-download-', delete=False) as stream:
            temporary = Path(stream.name)
            try:
                deadline = time.monotonic() + 900
                request = urllib.request.Request(url, headers={'User-Agent': 'ICODE-DOCX-renderer/1.0'})
                with opener.open(request, timeout=45) as response:
                    if urllib.parse.urlsplit(response.url).scheme != 'https':
                        raise ValueError('renderer response is not HTTPS')
                    size = 0
                    for block in iter(lambda: response.read(1024 * 1024), b''):
                        size += len(block)
                        if size > artifact['bytes'] or time.monotonic() > deadline:
                            raise ValueError('renderer download exceeded declared size/time limit')
                        stream.write(block)
                stream.flush()
                verify_artifact(temporary, artifact)
                temporary.replace(target)
                return target
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                    raise
                if index + 1 == len(artifact['urls']):
                    raise
                print('WARNING: renderer transport failed; retrying pinned trusted mirror', file=sys.stderr)
            finally:
                temporary.unlink(missing_ok=True)
    raise ValueError('renderer download failed')


def safe_unpack(tar: tarfile.TarFile, destination: Path) -> None:
    """Preflight and manually extract; no external paths, devices or setuid bits."""
    members = tar.getmembers()
    if sum(member.size for member in members) > MAX_EXTRACT_BYTES:
        raise ValueError('renderer archive exceeds unpacked size limit')
    for member in members:
        relative = PurePosixPath(member.name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('unsafe renderer archive path: ' + member.name)
        if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
            raise ValueError('unsupported renderer archive member: ' + member.name)
        if member.issym() or member.islnk():
            link = PurePosixPath(member.linkname)
            parent = destination / relative.parent if member.issym() else destination
            if link.is_absolute() or not (parent / member.linkname).resolve().is_relative_to(destination.resolve()):
                raise ValueError('unsafe renderer archive link: ' + member.name)
    # Files precede links so extracted writes cannot traverse a newly created link.
    for member in sorted(members, key=lambda m: m.issym() or m.islnk()):
        target = destination / member.name
        if not target.resolve().is_relative_to(destination.resolve()) or target.is_symlink():
            raise ValueError('renderer extraction path escaped bundle')
        if any(parent.is_symlink() for parent in target.parents if parent.is_relative_to(destination)):
            raise ValueError('renderer extraction traverses symlink')
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError('renderer archive link collides with existing file')
            if member.issym():
                target.symlink_to(member.linkname)
            else:
                os.link(destination / member.linkname, target)


def unpack_deb(package: Path, destination: Path, scratch: Path) -> None:
    # dpkg-deb only reads the verified archive; no installation, maintainer scripts or sudo.
    with tempfile.NamedTemporaryFile(dir=scratch, suffix='.tar') as stream:
        subprocess.run(['dpkg-deb', '--fsys-tarfile', str(package)], stdout=stream, stderr=subprocess.PIPE, check=True, timeout=240)
        stream.flush()
        with tarfile.open(stream.name) as tar:
            safe_unpack(tar, destination)


def unpack_artifact(package: Path, artifact: dict, stage: Path) -> None:
    root = stage / 'root'
    root.mkdir(exist_ok=True)
    if artifact['kind'] == 'deb':
        unpack_deb(package, root, stage)
        return
    count = 0
    with tarfile.open(package, 'r:gz') as tar:
        if sum(member.size for member in tar.getmembers()) > MAX_EXTRACT_BYTES:
            raise ValueError('LibreOffice archive exceeds unpacked size limit')
        for member in tar.getmembers():
            if not member.isfile() or not member.name.endswith('.deb') or '/DEBS/' not in member.name:
                continue
            # Desktop menu packages contain absolute host links; the headless bundle does not need them.
            if 'desktop-integration' in member.name or 'debian-menus' in member.name:
                continue
            with tempfile.NamedTemporaryFile(dir=stage, suffix='.deb') as stream:
                with tar.extractfile(member) as source:
                    shutil.copyfileobj(source, stream)
                stream.flush()
                unpack_deb(Path(stream.name), root, stage)
            count += 1
    if not count:
        raise ValueError('LibreOffice archive contains no renderer debs')


@contextmanager
def install_lock(root: Path):
    import fcntl  # Only the currently supported Linux recipe reaches this point.
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.install.lock').open('a') as stream:
        deadline = time.monotonic() + 120
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('another renderer install is still running')
                time.sleep(0.25)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def package_key(package: dict) -> str:
    # Launcher fixes must create a new runtime rather than silently retaining
    # an old executable after a skill upgrade with unchanged third-party pins.
    identity = {'package': package, 'installer_version': INSTALLER_VERSION,
                'launcher_sha256': sha256_file(TOOL_DIR / 'renderer_launch.py')}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def check_probes(bundle: Path, package: dict) -> None:
    for name, args in [('soffice', ['--version']), ('pdftoppm', ['-v'])]:
        run = subprocess.run([str(bundle / 'bin' / name), *args], capture_output=True, text=True, timeout=240)
        if run.returncode != 0 or not (run.stdout + run.stderr).strip():
            raise ValueError(name + ' renderer probe failed: ' + (run.stdout + run.stderr)[-2000:])
    env = os.environ.copy()
    env['LD_LIBRARY_PATH'] = str(bundle / package['library_dir'])
    for relative in package['executables'].values():
        executable = bundle / relative
        # soffice is a shell launcher; its sibling is the actual ELF program.
        if executable.name == 'soffice':
            executable = executable.with_name('soffice.bin')
        run = subprocess.run(['ldd', str(executable)], env=env, capture_output=True, text=True, timeout=30)
        if run.returncode != 0 or 'not found' in run.stdout:
            raise ValueError('renderer shared libraries unavailable: ' + (run.stdout + run.stderr)[-2000:])


def prepare(stage: Path, package: dict, key: str) -> dict:
    payload = {}
    links = {}
    for path in (stage / 'root').rglob('*'):
        if path.is_symlink():
            if not path.resolve().is_relative_to(stage):
                raise ValueError('extracted renderer link escaped bundle')
            links[str(path.relative_to(stage))] = os.readlink(path)
        elif path.is_file() and (path.stat().st_mode & 0o111 or '.so' in path.name):
            payload[str(path.relative_to(stage))] = sha256_file(path)
    for relative in package['executables'].values():
        path = owned_path(stage, relative)
        if not path.is_file() or not os.access(path, os.X_OK) or relative not in payload:
            raise ValueError('renderer executable missing after extraction: ' + relative)
    record = {'schema_version': 1, 'package_sha256': key, 'payload_sha256': payload,
              'symlinks': links,
              'executables': package['executables'], 'library_dir': package['library_dir'],
              'host_dependencies': 'general system libraries and fonts; no host LibreOffice or Poppler engine'}
    atomic_json(stage / 'payload_manifest.json', record)
    launcher = (TOOL_DIR / 'renderer_launch.py').read_text(encoding='utf-8').split('\n', 1)[1]
    if any(character in sys.executable for character in ('\n', ' ')):
        raise ValueError('renderer bootstrap Python path cannot be used in a shebang')
    (stage / 'bin').mkdir()
    for label in ('soffice', 'pdftoppm'):
        path = stage / 'bin' / label
        path.write_text('#!' + sys.executable + '\n' + launcher, encoding='utf-8')
        path.chmod(0o755)
    entry = {name: package[name] for name in ('os', 'arch', 'libc', 'glibc_min', 'kernel_min', 'cpu_flags') if name in package}
    entry.update(id=package['id'] + '-' + key[:16], bundle='.', soffice='bin/soffice', pdftoppm='bin/pdftoppm',
                 sha256={label: sha256_file(stage / 'bin' / label) for label in ('soffice', 'pdftoppm')},
                 payload_manifest='payload_manifest.json', payload_manifest_sha256=sha256_file(stage / 'payload_manifest.json'))
    validate_entry(entry, stage)
    check_probes(stage, package)
    atomic_json(stage / 'renderer_manifest.json', {'schema_version': 1, 'renderers': [entry]})
    return entry


def bootstrap(catalog: Path, root: Path, cache: Path, *, offline=False, check=False) -> dict:
    if root == Path(root.anchor) or root == Path.home().resolve() or root.is_relative_to(TOOL_DIR.parents[1]):
        raise ValueError('renderer root must be a dedicated directory outside the skill tree')
    current = host()
    package = next((entry for entry in catalog_entries(catalog) if matches(entry, current)), None)
    if package is None:
        return {'status': 'unavailable', 'host': current, 'reason': 'No pinned renderer recipe for this OS/CPU/glibc/kernel; visual_qa_pending'}
    key = package_key(package)
    bundle = root / (package['id'] + '-' + key[:16])
    def existing():
        data = json.loads((bundle / 'renderer_manifest.json').read_text())
        entry = data['renderers'][0]
        validate_entry(entry, bundle)
        record = json.loads(owned_path(bundle, entry['payload_manifest']).read_text())
        if entry['id'] != bundle.name or record['package_sha256'] != key:
            raise ValueError('existing renderer does not match package lock')
        check_probes(bundle, package)
        return entry
    if check:
        if not bundle.exists():
            return {'status': 'missing', 'host': current, 'bundle': str(bundle)}
        entry = existing()
        return {'status': 'ready', 'host': current, 'renderer': validate_entry(entry, bundle)}
    with install_lock(root):
        if bundle.exists():
            entry = existing()  # Fail closed: never overwrite an invalid existing runtime.
            state = 'ready'
        else:
            if shutil.disk_usage(root).free < package.get('min_free_bytes', 2500000000):
                raise ValueError('insufficient disk space for renderer staging')
            if not shutil.which('dpkg-deb') or not shutil.which('ldd'):
                raise ValueError('renderer extraction requires dpkg-deb and ldd; no host Office fallback')
            with tempfile.TemporaryDirectory(prefix='.renderer-stage-', dir=root) as temporary:
                stage = Path(temporary) / 'bundle'
                stage.mkdir()
                for artifact in package['artifacts']:
                    unpack_artifact(download(artifact, cache, offline), artifact, stage)
                entry = prepare(stage, package, key)
                stage.replace(bundle)
            state = 'installed'
        registry_path = root / 'renderer_manifest.json'
        registry = json.loads(registry_path.read_text()) if registry_path.exists() else {'schema_version': 1, 'renderers': []}
        if registry.get('schema_version') != 1 or not isinstance(registry.get('renderers'), list):
            raise ValueError('invalid persistent renderer registry; original file preserved')
        registered = {**entry, 'bundle': bundle.name}
        # Retain other platforms/recipes, replacing only this exact lock-addressed id.
        rows = [registered] + [row for row in registry['renderers'] if row['id'] != registered['id']]
        updated = {'schema_version': 1, 'renderers': rows}
        if registry != updated:
            atomic_json(registry_path, updated)
    return {'status': state, 'host': current, 'renderer': validate_entry(entry, bundle), 'registry': str(registry_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path(os.environ.get('ICODE_DOCX_RENDERER_CATALOG', CATALOG)))
    parser.add_argument('--runtime-root', type=Path, default=runtime_root())
    parser.add_argument('--package-cache', type=Path, help='verified archives for online reuse or offline installation')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--check', action='store_true', help='read-only runtime check; no downloads or registry writes')
    args = parser.parse_args()
    try:
        root = args.runtime_root.expanduser().resolve()
        cache = args.package_cache.expanduser().resolve() if args.package_cache else root / 'downloads'
        result = bootstrap(args.catalog, root, cache, offline=args.offline, check=args.check)
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as exc:
        print(json.dumps({'status': 'failed', 'reason': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 3 if result['status'] == 'missing' else 0


if __name__ == '__main__':
    raise SystemExit(main())
