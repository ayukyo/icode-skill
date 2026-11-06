"""Independent composition regressions using only private temporary fixtures."""
import os
from pathlib import Path

import pytest

from tools.outsource.common import OutsourceError, digest_json
from tools.outsource.store import Store
from tools.outsource import workspace
from tools.outsource import cli


def submitted(store, tmp_path, ticket, root, filename):
    contract = {
        'schema_version': 1, 'title': ticket, 'goal': 'fixture change',
        'requester': 'owner', 'constraints': [],
        'projects': [{'root': str(root), 'read_paths': ['.'],
                      'write_paths': [filename]}],
        'acceptance': [{'id': 'A', 'check': 'fixture'}],
    }
    snap = workspace.capture(contract['projects'], tmp_path / (ticket + '-snapshot'))
    store.mutate('publish', ticket, 'owner', 'owner-session', ticket + '-publish',
                 {'contract': contract, 'snapshot': snap})
    store.mutate('claim', ticket, 'worker', 'worker-session', ticket + '-claim', {})
    bindings = workspace.materialize(snap, tmp_path / (ticket + '-execution'))
    store.mutate('bind-workspace', ticket, 'worker', 'worker-session', ticket + '-bind',
                 {'workspace': bindings}, attempt=1)
    Path(bindings[str(root)], filename).write_text('new', encoding='utf-8')
    delivery = workspace.make_delivery(snap, bindings, tmp_path / (ticket + '-delivery'), [])
    return store.mutate('submit', ticket, 'worker', 'worker-session', ticket + '-submit',
                        {'delivery': delivery}, attempt=1)


def test_nested_non_git_integrations_must_exclude_each_other(tmp_path):
    """Target exclusivity includes parent/child roots, even for disjoint writes."""
    parent = tmp_path / 'source'
    child = parent / 'child'
    child.mkdir(parents=True)
    (parent / 'a.txt').write_text('old', encoding='utf-8')
    (child / 'b.txt').write_text('old', encoding='utf-8')
    store = Store(tmp_path / 'board', create=True)
    submitted(store, tmp_path, 'parent', parent, 'a.txt')
    submitted(store, tmp_path, 'child', child, 'b.txt')
    store.mutate('integration-start', 'parent', 'owner', 'owner-session', 'parent-start', {})
    with pytest.raises(OutsourceError, match='[Cc]onflict|exclus|reserved|held'):
        store.mutate('integration-start', 'child', 'owner', 'owner-session', 'child-start', {})


def test_parent_integration_blocks_new_child_claim(tmp_path):
    parent = tmp_path / 'source'
    child = parent / 'child'
    child.mkdir(parents=True)
    (parent / 'a.txt').write_text('old', encoding='utf-8')
    (child / 'b.txt').write_text('old', encoding='utf-8')
    store = Store(tmp_path / 'board', create=True)
    submitted(store, tmp_path, 'parent', parent, 'a.txt')
    store.mutate('integration-start', 'parent', 'owner', 'owner-session', 'parent-start', {})
    contract = {
        'schema_version': 1, 'title': 'child', 'goal': 'fixture change',
        'requester': 'owner', 'constraints': [],
        'projects': [{'root': str(child), 'read_paths': ['.'], 'write_paths': ['b.txt']}],
        'acceptance': [{'id': 'A', 'check': 'fixture'}],
    }
    snap = workspace.capture(contract['projects'], tmp_path / 'child-snapshot')
    store.mutate('publish', 'child', 'owner', 'owner-session', 'child-publish',
                 {'contract': contract, 'snapshot': snap})
    with pytest.raises(OutsourceError, match='[Cc]onflict|exclus|reserved|held'):
        store.mutate('claim', 'child', 'worker2', 'worker2-session', 'child-claim', {})


def test_user_resource_cannot_hide_internal_integration_reservation(tmp_path):
    parent = tmp_path / 'source'
    child = parent / 'child'
    child.mkdir(parents=True)
    (parent / 'a.txt').write_text('old', encoding='utf-8')
    (child / 'b.txt').write_text('old', encoding='utf-8')
    store = Store(tmp_path / 'board', create=True)
    parent_job = submitted(store, tmp_path, 'parent', parent, 'a.txt')
    submitted(store, tmp_path, 'child', child, 'b.txt')
    key = 'project/' + digest_json(parent_job['project_keys'][0])
    try:
        store.mutate('acquire', 'parent', 'owner', 'owner-session', 'ordinary-resource',
                     {'resources': [key]})
    except OutsourceError:
        return  # Rejecting user acquisition of an internal namespace is safe.
    try:
        store.mutate('integration-start', 'parent', 'owner', 'owner-session', 'parent-start', {})
    except OutsourceError:
        return  # Refusing to reuse an ordinary lock as an integration lock is safe.
    with pytest.raises(OutsourceError, match='[Cc]onflict|exclus|reserved|held'):
        store.mutate('integration-start', 'child', 'owner', 'owner-session', 'child-start', {})


@pytest.mark.skipif(os.name != 'nt', reason='Windows extended path alias')
def test_extended_path_alias_cannot_bind_source_as_execution(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_text('old', encoding='utf-8')
    store = Store(tmp_path / 'board', create=True)
    contract = {
        'schema_version': 1, 'title': 'alias', 'goal': 'fixture change',
        'requester': 'owner', 'constraints': [],
        'projects': [{'root': str(source), 'read_paths': ['.'], 'write_paths': ['a.txt']}],
        'acceptance': [{'id': 'A', 'check': 'fixture'}],
    }
    snap = workspace.capture(contract['projects'], tmp_path / 'snapshot')
    store.mutate('publish', 'alias', 'owner', 'owner-session', 'publish',
                 {'contract': contract, 'snapshot': snap})
    store.mutate('claim', 'alias', 'worker', 'worker-session', 'claim', {})
    alias = '\\\\?\\' + str(source)
    assert os.path.samefile(source, alias), 'fixture must alias the real source'
    with pytest.raises(OutsourceError, match='source|independent|alias'):
        store.mutate('bind-workspace', 'alias', 'worker', 'worker-session', 'bind',
                     {'workspace': {str(source): alias}}, attempt=1)


@pytest.mark.skipif(os.name != 'nt', reason='Windows short filename aliases')
def test_short_scope_alias_cannot_capture_private_directory(tmp_path):
    import ctypes
    source = tmp_path / 'source'
    private = source / '.codex'
    private.mkdir(parents=True)
    (private / 'secret.txt').write_text('fixture private bytes', encoding='utf-8')
    buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(private), buffer, len(buffer)):
        pytest.skip('short path aliases are unavailable on this volume')
    alias = Path(buffer.value).name
    if alias.lower() == private.name.lower():
        pytest.skip('filesystem does not assign a distinct short directory name')
    assert os.path.samefile(private, source / alias)
    project = {'root': str(source), 'read_paths': [alias], 'write_paths': []}
    with pytest.raises(OutsourceError, match='private|sensitive|alias'):
        workspace.capture([project], tmp_path / 'snapshot')


def test_invalid_manifest_shape_uses_protocol_error(tmp_path):
    snapshot = tmp_path / 'snapshot'
    snapshot.mkdir()
    (snapshot / 'manifest.json').write_text('[]', encoding='utf-8')
    with pytest.raises(OutsourceError, match='manifest|object|schema'):
        workspace.verify_snapshot(snapshot)


def integrated_case(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_text('old', encoding='utf-8')
    store = Store(tmp_path / 'board', create=True)
    job = submitted(store, tmp_path, 'rework', source, 'a.txt')
    store.mutate('integration-start', 'rework', 'owner', 'owner-session', 'start-first', {})
    receipt = workspace.integrate(job['snapshot'], job['delivery'], tmp_path / 'integration-first')
    store.mutate('integration-finish', 'rework', 'owner', 'owner-session', 'finish-first',
                 {'result': receipt, 'delivery_digest': job['delivery_digest']})
    return store, source, job


def test_direct_integrated_rework_requires_frozen_current_baseline(tmp_path):
    """The old no-baseline call must fail atomically instead of inventing running."""
    store, source, _ = integrated_case(tmp_path)
    before = store.get('rework')
    with pytest.raises(OutsourceError, match='snapshot|reference|baseline|manifest'):
        store.mutate('rework', 'rework', 'owner', 'owner-session', 'review-first',
                     {'reason': 'revise after actual integration'})
    assert store.get('rework') == before
    assert (source / 'a.txt').read_text(encoding='utf-8') == 'new'


def test_cli_integrated_rework_renews_baseline_and_completes(tmp_path):
    import json
    store, source, first = integrated_case(tmp_path)
    payload = tmp_path / 'rework.json'
    payload.write_text(json.dumps({'reason': 'revise after actual integration'}), encoding='utf-8')

    def command(name, actor, session, request, *arguments):
        return cli.run(cli.parser().parse_args([
            '--board', str(store.root), name, 'rework', '--actor', actor,
            '--session', session, '--request-id', request, *map(str, arguments),
        ]))

    rework = command('rework', 'owner', 'owner-session', 'review-first', '--payload', payload)
    assert rework['state'] == 'claimed'
    assert rework['attempt'] == first['attempt'] + 1
    assert rework['workspace'] is None
    assert rework['snapshot']['manifest_sha256'] != first['snapshot']['manifest_sha256']
    replay = command('rework', 'owner', 'owner-session', 'review-first', '--payload', payload)
    assert replay['event_id'] == rework['event_id']
    assert replay['revision'] == rework['revision']
    prepared = command('prepare', 'worker', 'worker-session', 'prepare-revised',
                       '--attempt', rework['attempt'], '--destination', tmp_path / 'execution-revised')
    work = Path(prepared['workspace'][str(source)])
    assert (work / 'a.txt').read_text(encoding='utf-8') == 'new'
    (work / 'a.txt').write_text('revised', encoding='utf-8')
    evidence = tmp_path / 'evidence.json'
    evidence.write_text(json.dumps({'files': []}), encoding='utf-8')
    command('submit', 'worker', 'worker-session', 'submit-revised',
            '--attempt', rework['attempt'], '--evidence', evidence)
    result = command('integrate', 'owner', 'owner-session', 'integrate-revised')
    assert result['state'] == 'integrated'
    assert (source / 'a.txt').read_text(encoding='utf-8') == 'revised'


@pytest.mark.parametrize('resumed_session', ['owner-session', 'resumed'])
def test_requester_resume_can_continue_partial_integration(tmp_path, monkeypatch, resumed_session):
    source = tmp_path / 'source'
    source.mkdir()
    for filename in ('a.txt', 'b.txt'):
        (source / filename).write_text('old', encoding='utf-8')
    store = Store(tmp_path / 'board', create=True)
    contract = {
        'schema_version': 1, 'title': 'resume', 'goal': 'fixture change',
        'requester': 'owner', 'constraints': [],
        'projects': [{'root': str(source), 'read_paths': ['.'], 'write_paths': ['.']}],
        'acceptance': [{'id': 'A', 'check': 'fixture'}],
    }
    snap = workspace.capture(contract['projects'], tmp_path / 'snapshot')
    store.mutate('publish', 'resume', 'owner', 'owner-session', 'publish',
                 {'contract': contract, 'snapshot': snap})
    store.mutate('claim', 'resume', 'worker', 'worker-session', 'claim', {})
    bindings = workspace.materialize(snap, tmp_path / 'execution')
    store.mutate('bind-workspace', 'resume', 'worker', 'worker-session', 'bind',
                 {'workspace': bindings}, attempt=1)
    for filename in ('a.txt', 'b.txt'):
        Path(bindings[str(source)], filename).write_text('new', encoding='utf-8')
    delivery = workspace.make_delivery(snap, bindings, tmp_path / 'delivery', [])
    store.mutate('submit', 'resume', 'worker', 'worker-session', 'submit',
                 {'delivery': delivery}, attempt=1)
    original = workspace._apply_change

    def interrupted(snapshot, delivery, change, output, index):
        if index == 1:
            raise KeyboardInterrupt('fixture interruption after first target write')
        return original(snapshot, delivery, change, output, index)

    def integrate(session):
        return cli.run(cli.parser().parse_args([
            '--board', str(store.root), 'integrate', 'resume', '--actor', 'owner',
            '--session', session, '--request-id', 'integrate',
        ]))

    monkeypatch.setattr(workspace, '_apply_change', interrupted)
    with pytest.raises(KeyboardInterrupt):
        integrate('owner-session')
    assert (source / 'a.txt').read_text(encoding='utf-8') == 'new'
    assert (source / 'b.txt').read_text(encoding='utf-8') == 'old'
    monkeypatch.setattr(workspace, '_apply_change', original)
    if resumed_session != 'owner-session':
        store.mutate('requester-resume', 'resume', 'owner', resumed_session, 'resume-requester',
                     {'previous_session': 'owner-session', 'stopped': True,
                      'reason': 'old process is stopped'})
    result = integrate(resumed_session)
    assert result['state'] == 'integrated'
    assert (source / 'b.txt').read_text(encoding='utf-8') == 'new'
