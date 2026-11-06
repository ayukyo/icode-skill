"""Frozen, bounded source copies and restartable integration without Git writes.

The copy covers declared read paths only. It is not an operating-system sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile

from .common import OutsourceError

MAX_FILES = 10000
MAX_BYTES = 100 * 1024 * 1024
_PRIVATE = {'.git', '.ssh', '.aws', '.azure', '.gnupg', '.kube', '.codex', '.claude', '.env',
            '.npmrc', '.netrc', '.pypirc', 'credentials', 'id_rsa', 'id_ed25519'}


def _fail(message):
    raise OutsourceError('workspace_invalid', message)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        _fail('invalid relative path')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or any(p in ('', '.') for p in value.split('/')):
        if value != '.':
            _fail('relative path traversal is forbidden')
    if any(p.lower() in _PRIVATE or p.lower().startswith('.env.') for p in path.parts):
        _fail('private or sensitive path is forbidden')
    return path.as_posix()


def _under(path, root):
    if path == root or root in path.parents:
        return True
    # Lexical spelling is insufficient for Windows short-name and volume aliases.
    # Compare every existing ancestor to the actual directory identity as well.
    if root.exists():
        for ancestor in (path, *path.parents):
            if ancestor.exists():
                try:
                    if os.path.samefile(ancestor, root):
                        return True
                except OSError as exc:
                    _fail('cannot establish independent path identity: ' + str(exc))
    return False


def _guard(path):
    """Reject links and Windows reparse points in every existing ancestor."""
    if not isinstance(path, (str, os.PathLike)) or not os.fspath(path):
        _fail('path must be a nonempty string or path object')
    if os.name == 'nt':
        spelling = os.fspath(path).replace('/', '\\')
        if spelling.startswith(('\\\\', '\\??\\')):
            _fail('Windows namespace or UNC alias paths are unsupported')
        for component in Path(spelling).parts[1:]:
            if component.rstrip(' .') != component or ':' in component:
                _fail('Windows trailing-dot, space, or stream alias paths are unsupported')
    path = Path(os.path.abspath(path))
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            _fail('symlink or reparse point is forbidden: ' + str(part))
    # Expand supported 8.3 spellings before private-path and containment checks.
    # This follows the original spelling only after all its links were rejected.
    resolved = path.resolve(strict=False)
    for part in (resolved, *resolved.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            _fail('symlink or reparse point is forbidden: ' + str(part))
    return resolved


def _root(value):
    path = Path(value)
    if not path.is_absolute():
        _fail('project root must be absolute')
    path = _guard(path)
    if not path.is_dir():
        _fail('project root must be an existing directory')
    # System trees and client secret/configuration directories are not source roots.
    lower = str(path).replace('\\', '/').lower()
    if any(p.lower() in _PRIVATE or p.lower().startswith('.env.') for p in path.parts) or lower.startswith(('/etc', '/proc', '/sys', '/dev', '/root', 'c:/windows', 'c:/programdata')):
        _fail('private or system project root is forbidden')
    return path


def _joined(root, relative):
    relative = _relative(relative)
    target = _guard(root / relative)
    if not _under(target, root):
        _fail('path escapes project root')
    return target


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, stat.S_IMODE(info.st_mode))


def _read_stable(path):
    path = _guard(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        _fail('only regular files are supported: ' + str(path))
    if before.st_size > MAX_BYTES:
        _fail('file exceeds byte budget')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if _signature(before) != _signature(opened):
            _fail('file changed before snapshot read')
        data = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    if _signature(before) != _signature(after) or _signature(before) != _signature(_guard(path).lstat()):
        _fail('file changed during stable snapshot read')
    if len(data) > MAX_BYTES:
        _fail('file exceeds byte budget')
    return data, {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'mode': stat.S_IMODE(before.st_mode)}


def _scan(root, reads=None, *, missing=False, exclude_git=False, max_files=MAX_FILES, max_bytes=MAX_BYTES):
    found = {}
    directories = set()
    total = 0
    def visit(target):
        nonlocal total
        target = _guard(target)
        rel = target.relative_to(root).as_posix()
        if exclude_git and '.git' in PurePosixPath(rel).parts:
            return
        _relative(rel)
        if not target.exists():
            if missing:
                return
            _fail('read path does not exist: ' + rel)
        if target.is_dir():
            directories.add(target)
            if len(directories) > max_files + 1:
                _fail('directory traversal budget exceeded')
            for child in sorted(target.iterdir()):
                visit(child)
        elif rel not in found:
            data, metadata = _read_stable(target)
            total += len(data)
            if len(found) >= max_files or total > max_bytes:
                _fail('snapshot budget exceeded')
            found[rel] = (data, metadata)
    for value in reads if reads is not None else ['.']:
        visit(_joined(root, value))
    return found


def _git(root):
    def read(*args):
        try:
            proc = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=10, check=False,
                                  stdin=subprocess.DEVNULL,
                                  env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
        except (OSError, subprocess.TimeoutExpired):
            return None
        return proc.stdout.decode('utf-8', errors='replace').strip() if proc.returncode == 0 else None
    head = read('rev-parse', 'HEAD')
    common = read('rev-parse', '--path-format=absolute', '--git-common-dir')
    return {'head': head, 'common_dir': common, 'dirty': read('status', '--porcelain=v1', '--untracked-files=all') if common else None}


def _new_dir(value, forbidden=()):
    path = _guard(value)
    if path.exists():
        _fail('output directory already exists: ' + str(path))
    if any(_under(path, root) or _under(root, path) for root in forbidden):
        _fail('output must be independent of source and execution roots')
    path.mkdir(parents=True)
    return path


def _atomic_json(path, value):
    path = _guard(path)
    handle, name = tempfile.mkstemp(prefix='.outsource-', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _object(output, data):
    name = 'objects/' + hashlib.sha256(data).hexdigest()
    path = output / name
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        with path.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    return name


def _reference(path, manifest):
    return {**manifest, 'manifest_path': str(path), 'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _is_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _fields(value, required, context):
    if not isinstance(value, dict) or not set(required).issubset(value):
        _fail(context + ' must be an object with required fields')


def _reference_shape(value):
    _fields(value, ('manifest_path', 'manifest_sha256'), 'manifest reference')
    if not isinstance(value['manifest_path'], str) or not value['manifest_path'] or not _is_digest(value['manifest_sha256']):
        _fail('manifest reference path or digest has invalid shape')


def _project_shapes(projects):
    if not isinstance(projects, list) or not projects or len(projects) > MAX_FILES:
        _fail('manifest projects must be a bounded nonempty list')
    for project in projects:
        _fields(project, ('root', 'read_paths', 'write_paths', 'git'), 'manifest project')
        if not isinstance(project['root'], str) or not project['root']:
            _fail('manifest project root must be a nonempty string')
        if any(not isinstance(project[key], list) or any(not isinstance(p, str) for p in project[key])
               for key in ('read_paths', 'write_paths')):
            _fail('manifest read_paths and write_paths must be string lists')
        _fields(project['git'], ('head', 'common_dir', 'dirty'), 'manifest Git identity')
        if any(value is not None and not isinstance(value, str) for value in project['git'].values()):
            _fail('manifest Git identity values must be strings or null')


def _file_shape(item, *, frozen=True, identity=False):
    _fields(item, ('sha256', 'size', 'mode'), 'manifest file metadata')
    if not _is_digest(item['sha256']) or type(item['size']) is not int or item['size'] < 0:
        _fail('manifest file hash or size has invalid shape')
    if type(item['mode']) is not int or not 0 <= item['mode'] <= 0o7777:
        _fail('manifest file mode has invalid shape')
    if frozen and (not isinstance(item.get('object'), str) or item['object'] != 'objects/' + item['sha256']):
        _fail('manifest frozen object has invalid shape')
    if identity:
        _fields(item, ('project', 'path'), 'manifest file identity')
        if type(item['project']) is not int or item['project'] < 0:
            _fail('manifest project index has invalid shape')
        _relative(item['path'])


def _manifest_shape(manifest, kind):
    if not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1 or manifest.get('type') != kind:
        _fail('invalid manifest object, type or schema')
    if not _is_digest(manifest.get('baseline_digest')):
        _fail('manifest baseline digest has invalid shape')
    if kind in ('snapshot', 'delivery'):
        _project_shapes(manifest.get('projects'))
    if kind == 'snapshot':
        if not isinstance(manifest.get('files'), list) or manifest.get('excluded') != ['.git']:
            _fail('manifest files or excluded paths must have valid list shape')
        if len(manifest['files']) > MAX_FILES:
            _fail('manifest file budget exceeded')
        for item in manifest['files']:
            _file_shape(item, identity=True)
    elif kind == 'delivery':
        _reference_shape(manifest.get('snapshot'))
        if type(manifest.get('change_count')) is not int or manifest['change_count'] < 0:
            _fail('manifest change_count must be a nonnegative integer')
        if not isinstance(manifest.get('changes'), list) or not isinstance(manifest.get('evidence'), list):
            _fail('manifest changes and evidence must be lists')
        if len(manifest['changes']) > MAX_FILES or len(manifest['evidence']) > MAX_FILES:
            _fail('manifest change or evidence budget exceeded')
        for change in manifest['changes']:
            _fields(change, ('project', 'path', 'old', 'new'), 'manifest change')
            if type(change['project']) is not int or change['project'] < 0:
                _fail('manifest change project index has invalid shape')
            _relative(change['path'])
            if change['old'] is not None:
                _file_shape(change['old'], frozen=False)
            if change['new'] is not None:
                _file_shape(change['new'])
        for evidence in manifest['evidence']:
            _file_shape(evidence)
            if not isinstance(evidence.get('path'), str) or not evidence['path']:
                _fail('manifest evidence path must be a nonempty string')
    elif kind == 'integration':
        for key in ('snapshot', 'delivery'):
            _reference_shape(manifest.get(key))
        if manifest.get('state') != 'completed' or type(manifest.get('completed_count')) is not int or manifest['completed_count'] < 0:
            _fail('manifest integration completion has invalid shape')
        if not isinstance(manifest.get('projects'), list) or not manifest['projects'] or len(manifest['projects']) > MAX_FILES:
            _fail('manifest integration projects must be a bounded nonempty list')
        for project in manifest['projects']:
            _fields(project, ('root', 'state'), 'manifest integration project')
            if not isinstance(project['root'], str) or not project['root'] or project['state'] != 'completed':
                _fail('manifest integration project has invalid shape')


def _load(reference, kind):
    if isinstance(reference, dict):
        value = reference.get('manifest_path', reference.get('receipt_path'))
        expected = reference.get('manifest_sha256', reference.get('receipt_sha256'))
        if not value:
            _fail('manifest reference requires manifest_path')
    else:
        value, expected = reference, None
    if expected is not None and not _is_digest(expected):
        _fail('manifest reference digest has invalid shape')
    path = _guard(value)
    if path.is_dir():
        path = path / ('receipt.json' if kind == 'integration' else 'manifest.json')
    data, _ = _read_stable(path)
    if expected and hashlib.sha256(data).hexdigest() != expected:
        _fail('manifest digest mismatch or tampering')
    try:
        manifest = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        _fail('invalid manifest JSON: ' + str(exc))
    _manifest_shape(manifest, kind)
    return path, manifest


def _read_document(path):
    try:
        return json.loads(_read_stable(path)[0])
    except (ValueError, UnicodeError) as exc:
        _fail('invalid recovery manifest JSON: ' + str(exc))


def _object_bytes(folder, item):
    relative = item.get('object')
    if not isinstance(relative, str) or relative != 'objects/' + item.get('sha256', ''):
        _fail('invalid frozen object path or hash')
    data, metadata = _read_stable(_joined(folder, relative))
    if metadata['sha256'] != item.get('sha256') or metadata['size'] != item.get('size'):
        _fail('frozen object digest mismatch or tampering')
    return data


def _budget_metadata(items):
    if len(items) > MAX_FILES:
        _fail('frozen manifest file budget exceeded')
    total = 0
    for item in items:
        if not isinstance(item, dict) or type(item.get('size')) is not int or item['size'] < 0:
            _fail('invalid frozen file size')
        if type(item.get('mode')) is not int or not 0 <= item['mode'] <= 0o7777:
            _fail('invalid frozen file mode')
        total += item['size']
        if total > MAX_BYTES:
            _fail('frozen manifest byte budget exceeded')


def _projects(projects):
    if not isinstance(projects, list) or not projects:
        _fail('at least one project is required')
    result = []
    for item in projects:
        _fields(item, ('root', 'read_paths', 'write_paths'), 'project')
        if not isinstance(item['root'], (str, os.PathLike)) or any(not isinstance(item[key], list) for key in ('read_paths', 'write_paths')):
            _fail('project root or scope lists have invalid shape')
        root = _root(item['root'])
        reads = [_relative(p) for p in item['read_paths']]
        writes = [_relative(p) for p in item['write_paths']]
        if any(not any(_in_scope(p, r) for r in reads) for p in writes):
            _fail('write scope must be inside read paths')
        if any(_under(root, Path(p['root'])) or _under(Path(p['root']), root) for p in result):
            _fail('project roots overlap')
        result.append({'root': str(root), 'read_paths': reads, 'write_paths': writes})
    if not any(p['read_paths'] for p in result):
        _fail('at least one read path is required')
    return result


def _in_scope(path, scope):
    return scope == '.' or path == scope or path.startswith(scope + '/')


def capture(projects, output_dir, *, max_files=MAX_FILES, max_bytes=MAX_BYTES):
    """Capture current bytes, including uncommitted files, into a new directory."""
    normalized = _projects(projects)
    gathered, count, size = [], 0, 0
    for project in normalized:
        project['git'] = _git(Path(project['root']))
        files = _scan(Path(project['root']), project['read_paths'], exclude_git=True,
                      max_files=max_files-count, max_bytes=max_bytes-size)
        count += len(files)
        size += sum(len(data) for data, _ in files.values())
        gathered.append(files)
    # Repeat the entire selected inventory to reject changes/additions during collection.
    for p, files in zip(normalized, gathered):
        if files != _scan(Path(p['root']), p['read_paths'], exclude_git=True, max_files=max_files, max_bytes=max_bytes):
            _fail('source changed during snapshot capture')
        if p['git'] != _git(Path(p['root'])):
            _fail('Git identity changed during snapshot capture')
    output = _new_dir(output_dir, [Path(p['root']) for p in normalized])
    records = []
    for index, files in enumerate(gathered):
        for name, (data, metadata) in sorted(files.items()):
            records.append({'project': index, 'path': name, **metadata, 'object': _object(output, data)})
    manifest = {'schema_version': 1, 'type': 'snapshot', 'projects': normalized, 'files': records, 'excluded': ['.git']}
    manifest['baseline_digest'] = _digest(manifest)
    _atomic_json(output / 'manifest.json', manifest)
    return _reference(output / 'manifest.json', manifest)


def verify_snapshot(reference):
    path, manifest = _load(reference, 'snapshot')
    body = {k: v for k, v in manifest.items() if k != 'baseline_digest'}
    if manifest.get('baseline_digest') != _digest(body):
        _fail('snapshot baseline digest mismatch')
    _budget_metadata(manifest['files'])
    normalized = _projects(manifest['projects'])
    keys = set()
    for item in manifest['files']:
        index, name = item['project'], _relative(item['path'])
        if not isinstance(index, int) or not 0 <= index < len(normalized):
            _fail('invalid snapshot project index')
        if (index, name) in keys or not any(_in_scope(name, p) for p in normalized[index]['read_paths']):
            _fail('duplicate or out of scope snapshot file')
        keys.add((index, name))
        _object_bytes(path.parent, item)
    return _reference(path, manifest)


validate_snapshot = verify_snapshot


def verify_snapshot_current(snapshot_ref):
    """Verify a new round is based on the actual selected source and Git identity."""
    snapshot = verify_snapshot(snapshot_ref)
    expected = {(item['project'], item['path']): _metadata(item) for item in snapshot['files']}
    for _ in range(2):
        actual = {}
        for index, project in enumerate(snapshot['projects']):
            root = _root(project['root'])
            actual.update({(index, name): metadata for name, (_, metadata) in
                           _scan(root, project['read_paths'], exclude_git=True).items()})
            if _git(root) != project['git']:
                _fail('current source Git baseline identity drift')
        if actual != expected:
            _fail('current source does not match snapshot baseline')
    return snapshot


def _bindings(snapshot, bindings):
    if not isinstance(bindings, dict) or set(bindings) != {p['root'] for p in snapshot['projects']}:
        _fail('workspace bindings must cover all source roots')
    # JSON object key order is not project identity; follow the frozen project list.
    roots = [_root(bindings[p['root']]) for p in snapshot['projects']]
    sources = [Path(p['root']) for p in snapshot['projects']]
    if any(_under(a, b) or _under(b, a) for a in roots for b in sources):
        _fail('workspace binding must be independent of source')
    if any(_under(a, b) or _under(b, a) for i, a in enumerate(roots) for b in roots[i+1:]):
        _fail('workspace bindings overlap')
    return roots


def materialize(snapshot_path, destination):
    snapshot = verify_snapshot(snapshot_path)
    existing = _guard(destination)
    marker = existing / 'materialization.json'
    identity = {k: snapshot[k] for k in ('manifest_path', 'manifest_sha256')}
    if existing.exists():
        if not marker.is_file():
            _fail('existing materialization is partial or has no recovery manifest')
        saved = _read_document(marker)
        _fields(saved, ('schema_version', 'snapshot', 'destination', 'bindings'), 'materialization manifest')
        if type(saved['schema_version']) is not int or saved['schema_version'] != 1:
            _fail('invalid materialization manifest schema')
        if saved.get('snapshot') != identity or saved.get('destination') != str(existing):
            _fail('existing materialization snapshot identity mismatch')
        bindings = saved.get('bindings')
        validate_workspace(snapshot, bindings)
        expected = {'materialization.json'} | {'project-' + str(i) for i in range(len(snapshot['projects']))}
        if {p.name for p in existing.iterdir()} != expected:
            _fail('existing materialization has unknown content')
        return bindings
    output = _new_dir(destination, [Path(p['root']) for p in snapshot['projects']] + [Path(snapshot['manifest_path']).parent])
    bindings = {}
    for index, project in enumerate(snapshot['projects']):
        root = output / ('project-' + str(index))
        root.mkdir()
        bindings[project['root']] = str(root)
    for item in snapshot['files']:
        root = Path(bindings[snapshot['projects'][item['project']]['root']])
        target = _joined(root, item['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_object_bytes(Path(snapshot['manifest_path']).parent, item))
        target.chmod(item['mode'])
    validate_workspace(snapshot, bindings)
    _atomic_json(output / 'materialization.json', {'schema_version': 1, 'snapshot': identity,
                                                'destination': str(output), 'bindings': bindings})
    return bindings


def validate_workspace(snapshot_path, bindings):
    snapshot = verify_snapshot(snapshot_path)
    roots = _bindings(snapshot, bindings)
    expected = {(f['project'], f['path']): {k: f[k] for k in ('sha256', 'size', 'mode')} for f in snapshot['files']}
    actual = {}
    for index, root in enumerate(roots):
        actual.update({(index, name): meta for name, (_, meta) in _scan(root).items()})
    if expected != actual:
        _fail('workspace does not match frozen snapshot baseline')
    return bindings


def make_delivery(snapshot_path, bindings, output_dir, evidence):
    snapshot = verify_snapshot(snapshot_path)
    roots = _bindings(snapshot, bindings)
    baseline = {(f['project'], f['path']): f for f in snapshot['files']}
    actual = {}
    for index, root in enumerate(roots):
        actual.update({(index, name): value for name, value in _scan(root).items()})
    _budget_metadata([metadata for _, metadata in actual.values()])
    changes = []
    for index, name in sorted(set(baseline) | set(actual)):
        old = baseline.get((index, name))
        new = actual.get((index, name))
        old_meta = {k: old[k] for k in ('sha256', 'size', 'mode')} if old else None
        new_meta = new[1] if new else None
        if old_meta == new_meta:
            continue
        if not any(_in_scope(name, p) for p in snapshot['projects'][index]['write_paths']):
            _fail('change is outside declared write scope: ' + name)
        changes.append({'project': index, 'path': name, 'old': old_meta, 'new': new_meta})
    if not isinstance(evidence, list):
        _fail('evidence must be a list of existing file paths')
    frozen_evidence = []
    for value in evidence:
        candidate = Path(value)
        if not candidate.is_absolute():
            _fail('evidence path must be absolute')
        parent = _root(candidate.parent)
        candidate = _joined(parent, candidate.name)
        frozen_evidence.append((str(candidate), *_read_stable(candidate)))
    if len(frozen_evidence) > MAX_FILES or sum(len(data) for _, data, _ in frozen_evidence) > MAX_BYTES:
        _fail('evidence budget exceeded')
    _budget_metadata([change['new'] for change in changes if change['new']] +
                     [metadata for _, _, metadata in frozen_evidence])
    # Verify execution did not change while evidence was captured.
    for index, root in enumerate(roots):
        if {name: value for (i, name), value in actual.items() if i == index} != _scan(root):
            _fail('execution changed during delivery freeze')
    output = _new_dir(output_dir, roots + [Path(p['root']) for p in snapshot['projects']])
    for change in changes:
        if change['new']:
            change['new'] = {**change['new'], 'object': _object(output, actual[(change['project'], change['path'])][0])}
    manifest = {'schema_version': 1, 'type': 'delivery', 'baseline_digest': snapshot['baseline_digest'],
                'snapshot': {'manifest_path': snapshot['manifest_path'], 'manifest_sha256': snapshot['manifest_sha256']},
                'projects': snapshot['projects'], 'change_count': len(changes), 'changes': changes,
                'evidence': [{'path': p, **meta, 'object': _object(output, data)} for p, data, meta in frozen_evidence]}
    _atomic_json(output / 'manifest.json', manifest)
    return _reference(output / 'manifest.json', manifest)


def verify_delivery(reference):
    path, manifest = _load(reference, 'delivery')
    snapshot = verify_snapshot(manifest['snapshot'])
    if manifest['baseline_digest'] != snapshot['baseline_digest'] or manifest['projects'] != snapshot['projects']:
        _fail('delivery baseline identity mismatch')
    if manifest['change_count'] != len(manifest['changes']):
        _fail('delivery change count mismatch')
    if len(manifest['changes']) > MAX_FILES:
        _fail('delivery change budget exceeded')
    _budget_metadata([change['new'] for change in manifest['changes'] if change['new']] + manifest['evidence'])
    baseline = {(f['project'], f['path']): f for f in snapshot['files']}
    seen = set()
    for change in manifest['changes']:
        index, name = change['project'], _relative(change['path'])
        if not isinstance(index, int) or not 0 <= index < len(snapshot['projects']):
            _fail('invalid delivery project index')
        if (index, name) in seen or not any(_in_scope(name, p) for p in snapshot['projects'][index]['write_paths']):
            _fail('duplicate or out of scope delivery change')
        seen.add((index, name))
        old = baseline.get((index, name))
        expected = {k: old[k] for k in ('sha256', 'size', 'mode')} if old else None
        if change['old'] != expected or not (change['old'] or change['new']):
            _fail('delivery old file hash does not match baseline')
        if change['new']:
            _object_bytes(path.parent, change['new'])
    for evidence in manifest['evidence']:
        _object_bytes(path.parent, evidence)
    return _reference(path, manifest)


validate_delivery = verify_delivery


def validate_delivery_workspace(snapshot_path, bindings, delivery_path):
    """Bind submitted bytes to the registered execution directories."""
    snapshot, delivery = verify_snapshot(snapshot_path), verify_delivery(delivery_path)
    if delivery['baseline_digest'] != snapshot['baseline_digest']:
        _fail('delivery workspace snapshot identity mismatch')
    roots = _bindings(snapshot, bindings)
    expected = {(f['project'], f['path']): _metadata(f) for f in snapshot['files']}
    for change in delivery['changes']:
        key = (change['project'], change['path'])
        if change['new']:
            expected[key] = _metadata(change['new'])
        else:
            expected.pop(key, None)
    actual = {}
    for index, root in enumerate(roots):
        actual.update({(index, name): meta for name, (_, meta) in _scan(root).items()})
    if actual != expected:
        _fail('registered workspace does not match frozen delivery')
    return bindings


def _current(path):
    path = _guard(path)
    if not path.exists():
        return None
    _, meta = _read_stable(path)
    return meta


def _metadata(value):
    return {k: value[k] for k in ('sha256', 'size', 'mode')} if value else None


def _precheck(snapshot, delivery, resuming):
    changes = {(c['project'], c['path']): c for c in delivery['changes']}
    baseline = {(f['project'], f['path']): f for f in snapshot['files']}
    for index, project in enumerate(snapshot['projects']):
        root = _root(project['root'])
        now_git = _git(root)
        if any(now_git[k] != project['git'][k] for k in ('head', 'common_dir')):
            _fail('target Git baseline identity drift')
        actual = _scan(root, project['read_paths'], missing=True, exclude_git=True)
        names = {name for i, name in baseline if i == index} | {name for i, name in changes if i == index} | set(actual)
        for name in names:
            value = actual.get(name)
            current = value[1] if value else None
            old = _metadata(baseline.get((index, name)))
            change = changes.get((index, name))
            new = _metadata(change['new']) if change else old
            if current != old and (not resuming or current != new):
                _fail('target baseline drift or third content state: ' + name)


def _apply_change(snapshot, delivery, change, output, index):
    root = _root(snapshot['projects'][change['project']]['root'])
    target = _joined(root, change['path'])
    old, new = change['old'], _metadata(change['new'])
    current = _current(target)
    if current == new:
        return
    if current != old:
        _fail('third content state before integration write: ' + change['path'])
    backup = output / 'backups' / str(index)
    backup.parent.mkdir(exist_ok=True)
    if old:
        data, metadata = _read_stable(target)
        if metadata != old:
            _fail('target changed before backup')
        if backup.exists():
            if hashlib.sha256(backup.read_bytes()).hexdigest() != old['sha256']:
                _fail('integration backup hash mismatch')
        else:
            with backup.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
    # Recheck after durable backup; never overwrite an unrelated concurrent edit.
    if _current(target) != old:
        _fail('target changed after backup')
    if new is None:
        target.unlink()
    else:
        data = _object_bytes(Path(delivery['manifest_path']).parent, change['new'])
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix='.outsource-', dir=target.parent)
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, new['mode'])
            if _current(target) != old:
                _fail('target changed before atomic replacement')
            os.replace(name, target)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    if _current(target) != new:
        _fail('integration output hash or mode mismatch')


def integrate(snapshot_path, delivery_path, output_dir):
    snapshot, delivery = verify_snapshot(snapshot_path), verify_delivery(delivery_path)
    if snapshot['baseline_digest'] != delivery['baseline_digest']:
        _fail('integration baseline identity mismatch')
    output = _guard(output_dir)
    forbidden = [Path(p['root']) for p in snapshot['projects']]
    if any(_under(output, p) or _under(p, output) for p in forbidden):
        _fail('integration output must be independent of source')
    journal_path = output / 'journal.json'
    identity = {'snapshot': {k: snapshot[k] for k in ('manifest_path', 'manifest_sha256')},
                'delivery': {k: delivery[k] for k in ('manifest_path', 'manifest_sha256')}}
    resuming = journal_path.exists()
    if resuming:
        journal = _read_document(journal_path)
        _fields(journal, ('schema_version', 'state', 'snapshot', 'delivery', 'completed', 'prepared'), 'integration journal')
        if type(journal['schema_version']) is not int or journal['schema_version'] != 1 or journal['state'] not in ('running', 'completed'):
            _fail('invalid integration journal schema or state')
        if not isinstance(journal['completed'], list) or any(type(i) is not int or not 0 <= i < delivery['change_count'] for i in journal['completed']):
            _fail('invalid integration journal completed list')
        if journal['prepared'] is not None and (type(journal['prepared']) is not int or not 0 <= journal['prepared'] < delivery['change_count']):
            _fail('invalid integration journal prepared index')
        if any(journal.get(k) != value for k, value in identity.items()):
            _fail('integration journal identity mismatch')
    else:
        if output.exists():
            _fail('integration directory exists without recoverable journal')
        _precheck(snapshot, delivery, False)
        output.mkdir(parents=True)
        journal = {'schema_version': 1, 'state': 'running', **identity, 'completed': [], 'prepared': None}
        _atomic_json(journal_path, journal)
    _precheck(snapshot, delivery, resuming)
    for index, change in enumerate(delivery['changes']):
        journal['prepared'] = index
        _atomic_json(journal_path, journal)
        _apply_change(snapshot, delivery, change, output, index)
        if index not in journal['completed']:
            journal['completed'].append(index)
        journal['prepared'] = None
        _atomic_json(journal_path, journal)
    journal['state'] = 'completed'
    _atomic_json(journal_path, journal)
    receipt_path = output / 'receipt.json'
    receipt = {'schema_version': 1, 'type': 'integration', 'state': 'completed', **identity,
               'baseline_digest': snapshot['baseline_digest'], 'completed_count': len(delivery['changes']),
               'projects': [{'root': p['root'], 'state': 'completed'} for p in snapshot['projects']]}
    if receipt_path.exists():
        if _read_document(receipt_path) != receipt:
            _fail('integration receipt identity mismatch')
    else:
        _atomic_json(receipt_path, receipt)
    result = _reference(receipt_path, receipt)
    result.update(receipt_path=str(receipt_path), receipt_sha256=result['manifest_sha256'])
    validate_integration(snapshot, delivery, result)
    return result


def validate_integration(snapshot_path, delivery_path, result):
    snapshot, delivery = verify_snapshot(snapshot_path), verify_delivery(delivery_path)
    path, receipt = _load(result, 'integration')
    if receipt.get('state') != 'completed' or receipt.get('completed_count') != delivery['change_count']:
        _fail('integration receipt is not completed')
    expected_projects = [{'root': p['root'], 'state': 'completed'} for p in snapshot['projects']]
    if receipt['baseline_digest'] != snapshot['baseline_digest'] or receipt['projects'] != expected_projects:
        _fail('integration receipt baseline or projects mismatch')
    for key, reference in (('snapshot', snapshot), ('delivery', delivery)):
        if receipt.get(key) != {k: reference[k] for k in ('manifest_path', 'manifest_sha256')}:
            _fail('integration receipt identity mismatch')
    _precheck(snapshot, delivery, True)
    for change in delivery['changes']:
        target = _joined(Path(snapshot['projects'][change['project']]['root']), change['path'])
        if _current(target) != _metadata(change['new']):
            _fail('integration receipt disagrees with actual target hashes')
    answer = _reference(path, receipt)
    answer.update(receipt_path=str(path), receipt_sha256=answer['manifest_sha256'])
    return answer


def verify_integration(result_ref, delivery_ref=None):
    _, receipt = _load(result_ref, 'integration')
    return validate_integration(receipt['snapshot'], delivery_ref or receipt['delivery'], result_ref)
