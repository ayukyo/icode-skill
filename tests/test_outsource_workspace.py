"""Real filesystem tests for the bounded outsourcing workspace."""
import hashlib
import importlib
import json
import os
from pathlib import Path

import pytest


def api():
    try:
        return importlib.import_module('tools.outsource.workspace')
    except ModuleNotFoundError:
        pytest.fail('workspace snapshot implementation is missing')


def project(tmp_path, name='source'):
    root = tmp_path / name
    (root / 'src').mkdir(parents=True)
    (root / 'src/main.txt').write_text('current dirty bytes', encoding='utf-8')
    (root / 'deps.txt').write_text('readonly dependency', encoding='utf-8')
    return {'root': str(root), 'read_paths': ['src', 'deps.txt'], 'write_paths': ['src']}


def test_snapshot_freezes_current_bytes_and_independent_materialization(tmp_path):
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    work = Path(bindings[p['root']])
    assert (work / 'src/main.txt').read_text() == 'current dirty bytes'
    (work / 'src/main.txt').write_text('isolated')
    assert Path(p['root'], 'src/main.txt').read_text() == 'current dirty bytes'
    assert w.verify_snapshot(snap)['baseline_digest'] == snap['baseline_digest']
    assert snap['manifest_sha256'] == hashlib.sha256(Path(snap['manifest_path']).read_bytes()).hexdigest()


def test_capture_rejects_path_escape_links_private_and_budget(tmp_path):
    w = api()
    p = project(tmp_path)
    with pytest.raises(Exception, match='path|relative|traversal'):
        w.capture([{**p, 'read_paths': ['../outside']}], tmp_path / 'escape')
    with pytest.raises(Exception, match='budget'):
        w.capture([p], tmp_path / 'budget', max_bytes=1)
    (Path(p['root']) / '.ssh').mkdir()
    (Path(p['root']) / '.ssh/id_rsa').write_text('secret')
    with pytest.raises(Exception, match='private|sensitive'):
        w.capture([{**p, 'read_paths': ['.ssh']}], tmp_path / 'private')
    link = Path(p['root']) / 'src/link'
    try:
        link.symlink_to(Path(p['root']) / 'deps.txt')
    except OSError:
        return  # Windows without symlink privilege; other path guards still run.
    with pytest.raises(Exception, match='link|reparse'):
        w.capture([p], tmp_path / 'linked')


def test_manifest_and_frozen_objects_tampering_rejected(tmp_path):
    w = api()
    snap = w.capture([project(tmp_path)], tmp_path / 'snapshot')
    item = snap['files'][0]
    Path(snap['manifest_path']).parent.joinpath(item['object']).write_bytes(b'tampered')
    with pytest.raises(Exception, match='digest|hash|tamper'):
        w.verify_snapshot(snap)


def test_delivery_full_tree_scope_add_delete_and_frozen_evidence(tmp_path):
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    work = Path(bindings[p['root']])
    (work / 'src/main.txt').unlink()
    (work / 'src/renamed.txt').write_text('renamed')
    evidence = tmp_path / 'verification.txt'
    evidence.write_text('pytest: 4 passed')
    delivery = w.make_delivery(snap, bindings, tmp_path / 'delivery', [str(evidence)])
    assert delivery['change_count'] == 2
    assert delivery['baseline_digest'] == snap['baseline_digest']
    evidence.write_text('changed after submit')
    assert w.verify_delivery(delivery)['evidence'][0]['sha256'] == hashlib.sha256(b'pytest: 4 passed').hexdigest()
    (work / 'unapproved.txt').write_text('outside declared reads and writes')
    with pytest.raises(Exception, match='scope|write'):
        w.make_delivery(snap, bindings, tmp_path / 'bad-delivery', [str(evidence)])


def test_integration_prechecks_readonly_dependency_and_all_projects(tmp_path):
    w = api()
    projects = [project(tmp_path, 'one'), project(tmp_path, 'two')]
    snap = w.capture(projects, tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    for p in projects:
        Path(bindings[p['root']], 'src/main.txt').write_text('delivered')
    d = w.make_delivery(snap, bindings, tmp_path / 'delivery', [])
    Path(projects[1]['root'], 'deps.txt').write_text('drift')
    with pytest.raises(Exception, match='baseline|drift'):
        w.integrate(snap, d, tmp_path / 'integration')
    assert Path(projects[0]['root'], 'src/main.txt').read_text() == 'current dirty bytes'


def test_integration_receipt_recovery_third_state_and_mode(tmp_path, monkeypatch):
    w = api()
    projects = [project(tmp_path, 'one'), project(tmp_path, 'two')]
    snap = w.capture(projects, tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    for p in projects:
        Path(bindings[p['root']], 'src/main.txt').write_text('delivered')
    d = w.make_delivery(snap, bindings, tmp_path / 'delivery', [])
    real = w._apply_change
    seen = []
    def interrupt(*args, **kwargs):
        if seen:
            raise RuntimeError('simulated interruption')
        seen.append(True)
        return real(*args, **kwargs)
    monkeypatch.setattr(w, '_apply_change', interrupt)
    with pytest.raises(RuntimeError, match='interruption'):
        w.integrate(snap, d, tmp_path / 'integration')
    journal = json.loads((tmp_path / 'integration/journal.json').read_text())
    assert journal['state'] != 'completed'
    assert len(journal['completed']) == 1
    monkeypatch.setattr(w, '_apply_change', real)
    Path(projects[1]['root'], 'src/main.txt').write_text('third state')
    with pytest.raises(Exception, match='third|drift|baseline'):
        w.integrate(snap, d, tmp_path / 'integration')
    Path(projects[1]['root'], 'src/main.txt').write_text('current dirty bytes')
    result = w.integrate(snap, d, tmp_path / 'integration')
    assert result['state'] == 'completed'
    assert result['completed_count'] == 2
    assert w.validate_integration(snap, d, result)['state'] == 'completed'
    for p in projects:
        assert Path(p['root'], 'src/main.txt').read_text() == 'delivered'
    assert w.integrate(snap, d, tmp_path / 'integration')['receipt_sha256'] == result['receipt_sha256']


def test_existing_output_and_invalid_binding_rejected(tmp_path):
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    with pytest.raises(Exception, match='exist'):
        w.capture([p], tmp_path / 'snapshot')
    with pytest.raises(Exception, match='binding|independent|source'):
        w.validate_workspace(snap, {p['root']: p['root']})

def test_materialize_retries_same_snapshot_without_overwrite(tmp_path):
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    first = w.materialize(snap, tmp_path / 'execution')
    assert w.materialize(snap, tmp_path / 'execution') == first
    Path(first[p['root']], 'src/main.txt').write_text('unknown edit')
    with pytest.raises(Exception, match='baseline|match|changed'):
        w.materialize(snap, tmp_path / 'execution')
    assert Path(first[p['root']], 'src/main.txt').read_text() == 'unknown edit'


def test_readonly_dependency_changes_and_receipt_tamper_are_rejected(tmp_path):
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    Path(bindings[p['root']], 'deps.txt').write_text('illegal')
    with pytest.raises(Exception, match='scope'):
        w.make_delivery(snap, bindings, tmp_path / 'bad', [])
    Path(bindings[p['root']], 'deps.txt').write_text('readonly dependency')
    Path(bindings[p['root']], 'src/main.txt').write_text('okay')
    delivery = w.make_delivery(snap, bindings, tmp_path / 'delivery', [])
    result = w.integrate(snap, delivery, tmp_path / 'integration')
    Path(p['root'], 'src/main.txt').write_text('later drift')
    with pytest.raises(Exception, match='drift|hash'):
        w.validate_integration(snap, delivery, result)


def test_git_identity_records_dirty_bytes_and_never_changes_index(tmp_path):
    import subprocess
    w = api()
    p = project(tmp_path)
    def git(*args):
        return subprocess.run(['git', '-C', p['root'], *args], check=True, capture_output=True).stdout
    git('init')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-m', 'fixture')
    index_before = Path(p['root'], '.git/index').read_bytes()
    Path(p['root'], 'src/main.txt').write_text('uncommitted live bytes')
    snap = w.capture([p], tmp_path / 'snapshot')
    assert snap['projects'][0]['git']['head'] == git('rev-parse', 'HEAD').decode().strip()
    assert 'main.txt' in snap['projects'][0]['git']['dirty']
    assert Path(p['root'], '.git/index').read_bytes() == index_before
    bindings = w.materialize(snap, tmp_path / 'execution')
    assert Path(bindings[p['root']], 'src/main.txt').read_text() == 'uncommitted live bytes'


def test_mode_only_change_is_delivered_and_integrated(tmp_path):
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    path = Path(bindings[p['root']], 'src/main.txt')
    original = path.stat().st_mode
    path.chmod(0o444 if os.name == 'nt' else 0o755)
    d = w.make_delivery(snap, bindings, tmp_path / 'delivery', [])
    assert d['change_count'] == 1
    assert d['changes'][0]['old']['sha256'] == d['changes'][0]['new']['sha256']
    try:
        result = w.integrate(snap, d, tmp_path / 'integration')
        assert result['state'] == 'completed'
        assert Path(p['root'], 'src/main.txt').stat().st_mode == path.stat().st_mode
    finally:
        path.chmod(original)
        Path(p['root'], 'src/main.txt').chmod(original)

def test_binding_mapping_order_does_not_change_project_identity(tmp_path):
    w = api()
    projects = [project(tmp_path, 'z-first'), project(tmp_path, 'a-second')]
    Path(projects[0]['root'], 'src/main.txt').write_text('first')
    Path(projects[1]['root'], 'src/main.txt').write_text('second')
    snap = w.capture(projects, tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    reverse = dict(reversed(list(bindings.items())))
    assert w.validate_workspace(snap, reverse) == reverse
    Path(reverse[projects[0]['root']], 'src/main.txt').write_text('changed first')
    d = w.make_delivery(snap, reverse, tmp_path / 'delivery', [])
    assert d['changes'][0]['project'] == 0


def test_capture_rejects_source_change_during_inventory(tmp_path, monkeypatch):
    w = api()
    p = project(tmp_path)
    scan = w._scan
    calls = []
    def mutate_after_read(*args, **kwargs):
        result = scan(*args, **kwargs)
        if not calls:
            calls.append(True)
            Path(p['root'], 'src/main.txt').write_text('concurrently changed')
        return result
    monkeypatch.setattr(w, '_scan', mutate_after_read)
    with pytest.raises(Exception, match='changed'):
        w.capture([p], tmp_path / 'snapshot')
    assert not (tmp_path / 'snapshot/manifest.json').exists()

def test_delivery_workspace_binding_is_checked_against_all_frozen_changes(tmp_path):
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    Path(bindings[p['root']], 'src/main.txt').write_text('frozen delivery')
    d = w.make_delivery(snap, bindings, tmp_path / 'delivery', [])
    assert hasattr(w, 'validate_delivery_workspace'), 'missing registered workspace revalidation'
    assert w.validate_delivery_workspace(snap, bindings, d) == bindings
    Path(bindings[p['root']], 'src/main.txt').write_text('later edit')
    with pytest.raises(Exception, match='workspace|delivery|match'):
        w.validate_delivery_workspace(snap, bindings, d)


def test_integration_add_delete_and_delivery_object_tamper(tmp_path):
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    Path(bindings[p['root']], 'src/main.txt').unlink()
    Path(bindings[p['root']], 'src/new.txt').write_text('new bytes')
    d = w.make_delivery(snap, bindings, tmp_path / 'delivery', [])
    result = w.integrate(snap, d, tmp_path / 'integration')
    assert result['completed_count'] == 2
    assert not Path(p['root'], 'src/main.txt').exists()
    assert Path(p['root'], 'src/new.txt').read_text() == 'new bytes'
    obj = next(c['new']['object'] for c in d['changes'] if c['new'])
    Path(d['manifest_path']).parent.joinpath(obj).write_text('tampered')
    with pytest.raises(Exception, match='digest|tamper'):
        w.verify_delivery(d)

def test_git_root_snapshot_excludes_metadata_but_execution_git_is_rejected(tmp_path):
    import subprocess
    w = api()
    p = project(tmp_path)
    p['read_paths'] = ['.']
    root = Path(p['root'])
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True).stdout
    git('init')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-m', 'fixture')
    Path(root, 'src/main.txt').write_text('dirty working bytes')
    status = git('status', '--porcelain=v1')
    index = (root / '.git/index').read_bytes()
    snap = w.capture([p], tmp_path / 'snapshot')
    assert snap['excluded'] == ['.git']
    assert all('.git' not in f['path'].split('/') for f in snap['files'])
    assert (root / '.git/index').read_bytes() == index
    assert git('status', '--porcelain=v1') == status
    bindings = w.materialize(snap, tmp_path / 'execution')
    work = Path(bindings[str(root)])
    (work / '.git').write_text('gitdir: unknown')
    with pytest.raises(Exception, match='private|sensitive|git'):
        w.make_delivery(snap, bindings, tmp_path / 'bad', [])


def test_verify_snapshot_enforces_manifest_budget(tmp_path, monkeypatch):
    w = api()
    snap = w.capture([project(tmp_path)], tmp_path / 'snapshot')
    monkeypatch.setattr(w, 'MAX_FILES', 1)
    with pytest.raises(Exception, match='budget'):
        w.verify_snapshot(snap)

def test_delivery_combined_code_and_evidence_budget(tmp_path, monkeypatch):
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    Path(bindings[p['root']], 'src/main.txt').write_bytes(b'n' * 3500)
    evidence = tmp_path / 'verification.txt'
    evidence.write_bytes(b'e' * 3500)
    monkeypatch.setattr(w, 'MAX_BYTES', 6000)
    with pytest.raises(Exception, match='budget'):
        w.make_delivery(snap, bindings, tmp_path / 'delivery', [str(evidence)])

def test_private_evidence_and_empty_directory_budget_are_rejected(tmp_path):
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    private = tmp_path / '.ssh'
    private.mkdir()
    secret = private / 'id_rsa'
    secret.write_text('private key')
    with pytest.raises(Exception, match='private|sensitive'):
        w.make_delivery(snap, bindings, tmp_path / 'delivery', [str(secret)])
    root = Path(p['root'])
    for i in range(3):
        (root / 'src' / str(i)).mkdir()
    with pytest.raises(Exception, match='budget'):
        w.capture([p], tmp_path / 'many-dirs', max_files=2)

@pytest.mark.skipif(os.name != 'nt', reason='Windows namespace alias')
def test_windows_namespace_alias_rejected_for_binding_and_output_boundaries(tmp_path):
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    source = Path(p['root'])
    alias = '\\\\?\\' + str(source)
    assert os.path.samefile(source, alias)
    with pytest.raises(Exception, match='alias|source|independent'):
        w.validate_workspace(snap, {str(source): alias})
    with pytest.raises(Exception, match='alias|source|independent'):
        w.materialize(snap, alias + '\\nested-execution')
    with pytest.raises(Exception, match='alias|source|independent'):
        w.capture([p], alias + '\\nested-snapshot')
    assert not (source / 'nested-execution').exists()
    assert not (source / 'nested-snapshot').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows path spelling aliases')
def test_windows_trailing_dot_alias_cannot_bypass_source_boundary(tmp_path):
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    source = Path(p['root'])
    alias = str(source) + '.'
    assert os.path.samefile(source, alias)
    with pytest.raises(Exception, match='alias|source|independent'):
        w.validate_workspace(snap, {str(source): alias})

@pytest.mark.skipif(os.name != 'nt', reason='Windows short-name aliases')
def test_windows_shortname_source_and_private_aliases_are_rejected(tmp_path):
    import ctypes
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    buffer = ctypes.create_unicode_buffer(32768)
    def short(path):
        length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
        assert length, 'GetShortPathNameW must succeed for existing fixture'
        return buffer.value
    source = Path(p['root'])
    alias = short(source)
    assert os.path.samefile(source, alias)
    with pytest.raises(Exception, match='alias|source|independent'):
        w.validate_workspace(snap, {str(source): alias})
    private = tmp_path / '.codex'
    private.mkdir()
    (private / 'file.txt').write_text('private')
    private_alias = short(private)
    if Path(private_alias).name.lower() == '.codex':
        pytest.skip('Filesystem does not expose a distinct private-directory 8.3 alias')
    with pytest.raises(Exception, match='private|sensitive'):
        w.capture([{'root': private_alias, 'read_paths': ['.'], 'write_paths': []}], tmp_path / 'bad')

def test_malformed_snapshot_manifest_shapes_raise_protocol_errors(tmp_path):
    import copy
    from tools.outsource.common import OutsourceError
    w = api()
    snap = w.capture([project(tmp_path)], tmp_path / 'snapshot')
    path = Path(snap['manifest_path'])
    original = json.loads(path.read_text())
    for root in ([], None, 1, 'text', True):
        path.write_text(json.dumps(root))
        with pytest.raises(OutsourceError, match='manifest|object|schema'):
            w.verify_snapshot(path)
    mutations = [
        ('files', None), ('files', {}), ('files', [None]), ('files', [{}]),
        ('projects', None), ('projects', {}), ('projects', [None]), ('projects', [{}]),
        ('excluded', None),
    ]
    for field, value in mutations:
        bad = copy.deepcopy(original)
        bad[field] = value
        bad['baseline_digest'] = w._digest({k: v for k, v in bad.items() if k != 'baseline_digest'})
        path.write_text(json.dumps(bad))
        with pytest.raises(OutsourceError):
            w.verify_snapshot(path)
    for field, value in [('git', None), ('git', []), ('read_paths', None), ('read_paths', {}), ('write_paths', 'src'), ('root', None)]:
        bad = copy.deepcopy(original)
        bad['projects'][0][field] = value
        bad['baseline_digest'] = w._digest({k: v for k, v in bad.items() if k != 'baseline_digest'})
        path.write_text(json.dumps(bad))
        with pytest.raises(OutsourceError):
            w.verify_snapshot(path)
    for field, value in [('project', True), ('path', {}), ('sha256', None), ('size', []), ('mode', None), ('object', [])]:
        bad = copy.deepcopy(original)
        bad['files'][0][field] = value
        bad['baseline_digest'] = w._digest({k: v for k, v in bad.items() if k != 'baseline_digest'})
        path.write_text(json.dumps(bad))
        with pytest.raises(OutsourceError):
            w.verify_snapshot(path)


def test_malformed_delivery_manifest_shapes_raise_protocol_errors(tmp_path):
    import copy
    from tools.outsource.common import OutsourceError
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    Path(bindings[p['root']], 'src/main.txt').write_text('new')
    evidence = tmp_path / 'evidence.txt'
    evidence.write_text('checked')
    d = w.make_delivery(snap, bindings, tmp_path / 'delivery', [str(evidence)])
    path = Path(d['manifest_path'])
    original = json.loads(path.read_text())
    for field, value in [('changes', None), ('changes', {}), ('changes', [None]), ('changes', [{}]),
                         ('evidence', None), ('evidence', {}), ('evidence', [None]), ('evidence', [{}]),
                         ('snapshot', []), ('snapshot', None), ('projects', []), ('change_count', True)]:
        bad = copy.deepcopy(original)
        bad[field] = value
        path.write_text(json.dumps(bad))
        with pytest.raises(OutsourceError):
            w.verify_delivery(path)
    for field, value in [('project', True), ('path', {}), ('new', {}), ('new', []), ('old', {})]:
        bad = copy.deepcopy(original)
        bad['changes'][0][field] = value
        path.write_text(json.dumps(bad))
        with pytest.raises(OutsourceError):
            w.verify_delivery(path)


def test_verify_snapshot_current_rejects_rework_snapshot_from_old_source(tmp_path):
    from tools.outsource.common import OutsourceError
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    assert hasattr(w, 'verify_snapshot_current'), 'missing current source baseline verification'
    assert w.verify_snapshot_current(snap)['baseline_digest'] == snap['baseline_digest']
    Path(p['root'], 'src/main.txt').write_text('already integrated candidate')
    with pytest.raises(OutsourceError, match='current|baseline|drift'):
        w.verify_snapshot_current(snap)

def test_recovery_documents_and_integration_manifest_reject_wrong_shapes(tmp_path, monkeypatch):
    import copy
    from tools.outsource.common import OutsourceError
    w = api()
    p = project(tmp_path)
    snap = w.capture([p], tmp_path / 'snapshot')
    bindings = w.materialize(snap, tmp_path / 'execution')
    marker = tmp_path / 'execution/materialization.json'
    marker.write_text('[]')
    with pytest.raises(OutsourceError):
        w.materialize(snap, tmp_path / 'execution')
    Path(bindings[p['root']], 'src/main.txt').write_text('new')
    d = w.make_delivery(snap, bindings, tmp_path / 'delivery', [])
    apply = w._apply_change
    def interrupt(*args, **kwargs):
        raise RuntimeError('interrupted')
    monkeypatch.setattr(w, '_apply_change', interrupt)
    with pytest.raises(RuntimeError):
        w.integrate(snap, d, tmp_path / 'integration')
    journal = tmp_path / 'integration/journal.json'
    original_journal = journal.read_text()
    journal.write_text('[]')
    with pytest.raises(OutsourceError):
        w.integrate(snap, d, tmp_path / 'integration')
    journal.write_text(original_journal)
    monkeypatch.setattr(w, '_apply_change', apply)
    result = w.integrate(snap, d, tmp_path / 'integration')
    receipt = Path(result['receipt_path'])
    original = json.loads(receipt.read_text())
    for field, value in [('projects', []), ('projects', [None]), ('snapshot', None),
                         ('delivery', []), ('completed_count', True), ('baseline_digest', '0' * 64)]:
        bad = copy.deepcopy(original)
        bad[field] = value
        receipt.write_text(json.dumps(bad))
        with pytest.raises(OutsourceError):
            w.validate_integration(snap, d, receipt)

def test_git_queries_do_not_inherit_protocol_stdin(tmp_path, monkeypatch):
    w = api()
    p = project(tmp_path)
    run = w.subprocess.run
    observed = []
    def checked_run(*args, **kwargs):
        if kwargs.get('stdin') != w.subprocess.DEVNULL:
            pytest.fail('Git reads must detach protocol stdin')
        observed.append(args[0])
        return run(*args, **kwargs)
    monkeypatch.setattr(w.subprocess, 'run', checked_run)
    w.capture([p], tmp_path / 'snapshot')
    assert observed
