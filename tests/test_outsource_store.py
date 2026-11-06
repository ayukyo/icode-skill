from pathlib import Path
import importlib
import multiprocessing
import sqlite3
import pytest

from tools.outsource.common import OutsourceError, digest_json, sha256_file
from tools.outsource.store import Store


def test_store_implementation_is_available():
    assert Path('tools/outsource/store.py').is_file()
    assert importlib.import_module('tools.outsource.store').Store


def contract_for(root, write='src'):
    return {'schema_version': 1, 'title': 'Task', 'goal': 'Change source',
            'requester': 'owner', 'projects': [{'root': str(root), 'read_paths': ['src'],
            'write_paths': [write] if write else []}], 'acceptance': [{'id': 'A', 'check': 'tests'}],
            'constraints': [], 'resources': ['device/camera'], 'depends_on': []}


@pytest.fixture
def board(tmp_path):
    root = tmp_path / 'project'
    (root / 'src').mkdir(parents=True)
    (root / 'src' / 'a.txt').write_text('old', encoding='utf-8')
    return Store(tmp_path / 'board', create=True), root


def publish(store, root, ticket='job', **extra):
    from tools.outsource.workspace import capture
    contract = contract_for(root)
    contract.update(extra)
    snapshot = capture(contract['projects'], store.root / 'tickets' / ticket / 'snapshot')
    return store.mutate('publish', ticket, 'owner', 'owner-session', 'publish-' + ticket,
                        {'contract': contract, 'snapshot': snapshot})


def change(store, action, payload=None, actor='worker', session='worker-session', ticket='job', attempt=1, request=None, **kw):
    return store.mutate(action, ticket, actor, session, request or action, payload or {}, attempt=attempt, **kw)


def test_missing_board_is_not_created(tmp_path):
    with pytest.raises(OutsourceError, match='board'):
        Store(tmp_path / 'absent')
    assert not (tmp_path / 'absent').exists()


def test_publish_replay_revision_query_and_precise_resolve(board):
    store, root = board
    first = publish(store, root)
    assert first['state'] == 'published' and first['revision'] == 1
    original = store.db_path.read_bytes()
    assert store.resolve(store.root / 'tickets' / 'job' / 'request.md') == 'job'
    assert store.get('job')['contract']['title'] == 'Task'
    assert store.list(requester='owner')[0]['ticket_id'] == 'job'
    assert store.events()[0]['action'] == 'publish'
    assert store.db_path.read_bytes() == original
    assert store.lookup_request('owner', 'publish-job')['ticket_id'] == 'job'
    with pytest.raises(OutsourceError) as error:
        change(store, 'claim', expected_revision=0)
    assert error.value.code == 'revision_conflict'
    claimed = change(store, 'claim')
    assert claimed['attempt'] == 1
    assert change(store, 'claim') == claimed
    with pytest.raises(OutsourceError) as error:
        change(store, 'claim', {'different': True})
    assert error.value.code == 'request_conflict'


def test_resource_set_is_atomic_and_aliases_and_parent_conflict(board):
    store, root = board
    publish(store, root, resources=['device', 'browser/main'])
    change(store, 'claim')
    change(store, 'register-resource', {'resource': 'device/camera', 'aliases': ['cam0']}, actor='owner', session='owner-session')
    change(store, 'acquire', {'resources': ['cam0']}, actor='owner', session='owner-session', request='owner-lock')
    with pytest.raises(OutsourceError) as error:
        change(store, 'acquire', {'resources': ['browser/main', 'device']})
    assert error.value.code == 'resource_conflict'
    assert not any(row['resource'] == 'browser/main' for row in store.get('job')['resources'])
    with pytest.raises(OutsourceError):
        change(store, 'release', {'resources': ['cam0']}, actor='owner', session='owner-session')
    change(store, 'release', {'resources': ['cam0'], 'reason': 'operation stopped'}, actor='owner', session='owner-session', request='unlock')
    change(store, 'acquire', {'resources': ['device']}, request='worker-lock')


def test_dependencies_and_overlapping_file_scopes(board):
    store, root = board
    publish(store, root, 'dep')
    publish(store, root, 'job', depends_on=['dep'])
    with pytest.raises(OutsourceError) as error:
        change(store, 'claim')
    assert error.value.code == 'dependency_blocked'
    publish(store, root, 'other')
    change(store, 'claim', ticket='dep', request='claim-dep')
    with pytest.raises(OutsourceError) as error:
        change(store, 'claim', ticket='other', request='claim-other')
    assert error.value.code == 'scope_conflict'


def test_participant_messages_cursor_and_old_attempt(board):
    store, root = board
    publish(store, root)
    change(store, 'claim')
    with pytest.raises(OutsourceError):
        change(store, 'message', {'body': 'bad'}, actor='stranger')
    message = change(store, 'message', {'body': 'ready'})
    inbox = store.inbox('owner')
    assert inbox[-1]['event_id'] == message['event_id']
    with pytest.raises(OutsourceError):
        change(store, 'ack', {'event_id': message['event_id'] + 50}, actor='owner', session='owner-session')
    change(store, 'ack', {'event_id': message['event_id']}, actor='owner', session='owner-session', request='ack-valid')
    assert store.inbox('owner') == []
    change(store, 'handoff', {'stopped': True, 'to_actor': 'worker2', 'to_session': 's2', 'reason': 'handoff'}, actor='owner', session='owner-session')
    with pytest.raises(OutsourceError) as error:
        change(store, 'heartbeat')
    assert error.value.code == 'stale_attempt'
    change(store, 'heartbeat', actor='worker2', session='s2', attempt=2, request='new-heartbeat')


def test_cancel_requires_stopped_and_released_resources(board):
    store, root = board
    publish(store, root)
    change(store, 'claim')
    change(store, 'acquire', {'resources': ['device/camera']})
    change(store, 'request-cancel', {'reason': 'stop'}, actor='owner', session='owner-session')
    with pytest.raises(OutsourceError):
        change(store, 'confirm-cancel', {'stopped': True, 'reason': 'stopped'})
    change(store, 'release', {'resources': ['device/camera'], 'reason': 'operation stopped'})
    assert change(store, 'confirm-cancel', {'stopped': True, 'reason': 'stopped'})['state'] == 'cancelled'


def _claim_process(root, barrier, queue, actor):
    store = Store(root)
    barrier.wait()
    try:
        result = store.mutate('claim', 'job', actor, actor, actor, {})
        queue.put(('ok', result['claimant']))
    except OutsourceError as error:
        queue.put(('error', error.code))


def test_real_process_claim_has_exactly_one_winner(board):
    store, root = board
    publish(store, root)
    context = multiprocessing.get_context('spawn')
    barrier = context.Barrier(3)
    queue = context.Queue()
    workers = [context.Process(target=_claim_process, args=(str(store.root), barrier, queue, 'w' + str(i))) for i in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait(timeout=15)
    outcomes = [queue.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0
    assert [row[0] for row in outcomes].count('ok') == 1
    assert len([row for row in store.events() if row['action'] == 'claim']) == 1


def test_event_insert_failure_rolls_back_claim(board):
    store, root = board
    publish(store, root)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(OutsourceError):
        change(store, 'claim')
    assert store.get('job')['state'] == 'published'
    assert store.lookup_request('worker', 'claim') is None



def test_request_envelope_contract_validation_and_doctor(board):
    from tools.outsource.store import validate_contract, normalize_contract
    store, root = board
    contract = contract_for(root)
    assert normalize_contract(contract) == contract
    assert validate_contract(contract, 'owner') is None
    contract['unknown'] = True
    with pytest.raises(OutsourceError):
        validate_contract(contract, 'owner')
    publish(store, root)
    envelope = store.request_envelope('owner', 'publish-job')
    assert envelope['action'] == 'publish' and envelope['ticket'] == 'job'
    assert envelope['payload_digest'] == digest_json(store.events()[0]['payload'])
    assert store.doctor()['ok'] is True


def delivery_for(store, root, tmp_path, changed=True):
    from tools.outsource import workspace
    job = store.get('job')
    bindings = workspace.materialize(job['snapshot'], tmp_path / 'execution')
    change(store, 'bind-workspace', {'workspace': bindings})
    if changed:
        (Path(bindings[str(root)]) / 'src/a.txt').write_text('new', encoding='utf-8')
    evidence = tmp_path / 'evidence.txt'
    evidence.write_text('tests pass', encoding='utf-8')
    delivery = workspace.make_delivery(job['snapshot'], bindings, tmp_path / 'delivery', [str(evidence)])
    return delivery, {'path': str(evidence), 'sha256': sha256_file(evidence)}


def test_delivery_integration_and_acceptance_are_verified(board, tmp_path):
    from tools.outsource import workspace
    store, root = board
    publish(store, root)
    change(store, 'claim')
    delivery, evidence = delivery_for(store, root, tmp_path)
    submitted = change(store, 'submit', {'delivery': delivery})
    checks = [{'id': 'A', 'pass': True, 'evidence': evidence}]
    accept = {'delivery_digest': submitted['delivery_digest'], 'checks': checks}
    with pytest.raises(OutsourceError):
        change(store, 'accept', accept, actor='owner', session='owner-session')
    change(store, 'integration-start', actor='owner', session='owner-session')
    with pytest.raises(OutsourceError):
        change(store, 'integration-finish', {'result': {'ok': True}, 'delivery_digest': submitted['delivery_digest']}, actor='owner', session='owner-session')
    result = workspace.integrate(store.get('job')['snapshot'], delivery, tmp_path / 'integration')
    change(store, 'integration-finish', {'result': result, 'delivery_digest': submitted['delivery_digest']}, actor='owner', session='owner-session', request='finish')
    Path(evidence['path']).write_text('tampered', encoding='utf-8')
    with pytest.raises(OutsourceError):
        change(store, 'accept', accept, actor='owner', session='owner-session')
    Path(evidence['path']).write_text('tests pass', encoding='utf-8')
    assert change(store, 'accept', accept, actor='owner', session='owner-session', request='accept-valid')['state'] == 'accepted'


def test_no_code_requires_actual_empty_delivery(board, tmp_path):
    store, root = board
    publish(store, root)
    change(store, 'claim')
    delivery, evidence = delivery_for(store, root, tmp_path, changed=False)
    submitted = change(store, 'submit', {'delivery': delivery})
    assert change(store, 'accept', {'delivery_digest': submitted['delivery_digest'], 'no_code': True, 'checks': [{'id': 'A', 'pass': True, 'evidence': evidence}]}, actor='owner', session='owner-session')['state'] == 'accepted'


def test_snapshot_and_contract_tampering_are_rejected(board):
    store, root = board
    published = publish(store, root)
    manifest = Path(published['snapshot']['manifest_path'])
    manifest.write_text('{}', encoding='utf-8')
    with pytest.raises(OutsourceError):
        change(store, 'claim')
    assert store.get('job')['state'] == 'published'
    assert store.doctor()['ok'] is False


def test_publish_replay_does_not_recheck_changed_snapshot(board):
    store, root = board
    published = publish(store, root)
    payload = store.events()[0]['payload']
    Path(published['snapshot']['manifest_path']).write_text('{}', encoding='utf-8')
    assert store.mutate('publish', 'job', 'owner', 'owner-session', 'publish-job', payload) == published


def test_unclaimed_requester_can_confirm_cancel(board):
    store, root = board
    publish(store, root)
    change(store, 'request-cancel', actor='owner', session='owner-session')
    assert change(store, 'confirm-cancel', {'stopped': True, 'reason': 'no worker started'}, actor='owner', session='owner-session')['state'] == 'cancelled'


def test_dot_read_scope_covers_project_files(board):
    from tools.outsource.store import validate_contract
    _, root = board
    contract = contract_for(root)
    contract['projects'][0]['read_paths'] = ['.']
    contract['projects'][0]['write_paths'] = ['src/a.txt']
    assert validate_contract(contract, 'owner') is None


def test_integration_reservation_prevents_new_claim(board, tmp_path):
    store, root = board
    publish(store, root)
    change(store, 'claim')
    delivery, _ = delivery_for(store, root, tmp_path)
    change(store, 'submit', {'delivery': delivery})
    change(store, 'integration-start', actor='owner', session='owner-session')
    (root / 'src' / 'b.txt').write_text('different', encoding='utf-8')
    from tools.outsource import workspace
    contract = contract_for(root)
    contract['projects'][0]['write_paths'] = ['src/b.txt']
    snapshot = workspace.capture(contract['projects'], store.root / 'tickets' / 'next' / 'snapshot')
    store.mutate('publish', 'next', 'owner', 'owner-session', 'publish-next', {'contract': contract, 'snapshot': snapshot})
    with pytest.raises(OutsourceError):
        change(store, 'claim', ticket='next', request='claim-next')


def test_two_submitted_independent_tasks_can_begin_integration(board, tmp_path):
    from tools.outsource import workspace
    store, root = board
    (root / 'src/b.txt').write_text('other', encoding='utf-8')
    for ticket, filename in [('first', 'a.txt'), ('second', 'b.txt')]:
        contract = contract_for(root)
        contract['projects'][0]['write_paths'] = ['src/' + filename]
        snapshot = workspace.capture(contract['projects'], store.root / 'tickets' / ticket / 'snapshot')
        store.mutate('publish', ticket, 'owner', 'owner-session', 'publish-' + ticket, {'contract': contract, 'snapshot': snapshot})
        change(store, 'claim', ticket=ticket, request='claim-' + ticket)
        bindings = workspace.materialize(snapshot, tmp_path / ('exec-' + ticket))
        change(store, 'bind-workspace', {'workspace': bindings}, ticket=ticket, request='bind-' + ticket)
        (Path(bindings[str(root)]) / 'src' / filename).write_text('new', encoding='utf-8')
        delivery = workspace.make_delivery(snapshot, bindings, tmp_path / ('delivery-' + ticket), [])
        change(store, 'submit', {'delivery': delivery}, ticket=ticket, request='submit-' + ticket)
    started = change(store, 'integration-start', ticket='first', actor='owner', session='owner-session', request='start-first')
    assert started['state'] == 'integrating'
    with pytest.raises(OutsourceError):
        change(store, 'integration-start', ticket='second', actor='owner', session='owner-session', request='start-second')


def test_nested_git_project_roots_reserve_same_file(board):
    import subprocess
    from tools.outsource import workspace
    store, root = board
    subprocess.run(['git', 'init', str(root)], check=True, capture_output=True)
    contract = contract_for(root)
    contract['projects'][0]['write_paths'] = ['src/a.txt']
    snap = workspace.capture(contract['projects'], store.root / 'tickets' / 'first' / 'snapshot')
    store.mutate('publish', 'first', 'owner', 'owner-session', 'publish-first', {'contract': contract, 'snapshot': snap})
    change(store, 'claim', ticket='first', request='claim-first')
    contract['projects'] = [{'root': str(root / 'src'), 'read_paths': ['.'], 'write_paths': ['a.txt']}]
    snap = workspace.capture(contract['projects'], store.root / 'tickets' / 'second' / 'snapshot')
    store.mutate('publish', 'second', 'owner', 'owner-session', 'publish-second', {'contract': contract, 'snapshot': snap})
    with pytest.raises(OutsourceError) as error:
        change(store, 'claim', ticket='second', request='claim-second')
    assert error.value.code == 'scope_conflict'


def test_requester_resume_preserves_integration_locks_and_rejects_old_session(board, tmp_path):
    store, root = board
    publish(store, root)
    change(store, 'claim')
    change(store, 'acquire', {'resources': ['device/camera']}, actor='owner', session='owner-session', request='owner-acquire')
    with pytest.raises(OutsourceError):
        change(store, 'requester-resume', {'previous_session': 'owner-session', 'stopped': True, 'reason': 'old session stopped'}, actor='owner', session='resumed', request='resume-resource')
    change(store, 'release', {'resources': ['device/camera'], 'reason': 'stopped'}, actor='owner', session='owner-session')
    delivery, _ = delivery_for(store, root, tmp_path)
    change(store, 'submit', {'delivery': delivery})
    change(store, 'integration-start', actor='owner', session='owner-session')
    result = change(store, 'requester-resume', {'previous_session': 'owner-session', 'stopped': True, 'reason': 'old process stopped'}, actor='owner', session='resumed', request='resume')
    assert result['requester_session'] == 'resumed'
    assert all(resource['session'] == 'resumed' for resource in result['resources'])
    with pytest.raises(OutsourceError):
        change(store, 'message', {'body': 'old'}, actor='owner', session='owner-session')
    with pytest.raises(OutsourceError):
        store.mutate('publish', 'job', 'owner', 'resumed', 'publish-job', store.events()[0]['payload'])


def test_normalize_contract_canonicalizes_project_paths(board):
    from tools.outsource.store import normalize_contract
    _, root = board
    contract = contract_for(root)
    contract['projects'][0]['root'] = str(root).replace('\\', '/')
    contract['projects'][0]['write_paths'] = ['src\\a.txt']
    normalized = normalize_contract(contract)
    assert normalized['projects'][0]['root'] == str(root.resolve())
    assert normalized['projects'][0]['write_paths'] == ['src/a.txt']


@pytest.mark.parametrize('field,value', [('constraints', [False]), ('schema_version', True), ('acceptance', [{'id': 'A', 'check': 'tests', 'extra': True}]), ('resources', ['device', 'device']), ('depends_on', ['dep', 'dep'])])
def test_contract_matches_machine_schema(board, field, value):
    from tools.outsource.store import validate_contract
    _, root = board
    contract = contract_for(root)
    contract[field] = value
    with pytest.raises(OutsourceError):
        validate_contract(contract)


def test_submit_rejects_delivery_from_other_execution_tree(board, tmp_path):
    from tools.outsource import workspace
    store, root = board
    publish(store, root)
    change(store, 'claim')
    delivery, _ = delivery_for(store, root, tmp_path, changed=True)
    bound = Path(store.get('job')['workspace'][str(root)]) / 'src/a.txt'
    bound.write_text('different actual content', encoding='utf-8')
    with pytest.raises(OutsourceError):
        change(store, 'submit', {'delivery': delivery})
    assert store.get('job')['state'] == 'running'


def test_board_owner_and_schema_are_checked_without_writes(board):
    store, _ = board
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE meta SET value='OtherOS' WHERE key='owner_os'")
    before = store.db_path.read_bytes()
    for operation in [lambda: store.list(), lambda: Store(store.root), lambda: Store(store.root, create=True), lambda: store.mutate('claim', 'job', 'x', 's', 'r', {})]:
        with pytest.raises(OutsourceError) as error:
            operation()
        assert error.value.code == 'owner_os_mismatch'
        assert store.db_path.read_bytes() == before


def test_unknown_board_schema_is_not_migrated(board):
    store, _ = board
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    before = store.db_path.read_bytes()
    with pytest.raises(OutsourceError):
        Store(store.root, create=True)
    assert store.db_path.read_bytes() == before


def test_ack_and_heartbeat_do_not_create_inbox_loops(board):
    store, root = board
    publish(store, root)
    change(store, 'claim')
    worker_before = store.inbox('worker')
    owner_before = store.inbox('owner')
    change(store, 'heartbeat')
    assert store.inbox('owner') == owner_before
    event_id = owner_before[-1]['event_id']
    change(store, 'ack', {'event_id': event_id}, actor='owner', session='owner-session')
    assert store.inbox('worker') == worker_before


def test_database_symlink_is_rejected_before_initialization(tmp_path):
    target = tmp_path / 'target.db'
    target.write_bytes(b'preserve')
    board_root = tmp_path / 'board'
    board_root.mkdir()
    try:
        (board_root / 'board.sqlite3').symlink_to(target)
    except OSError:
        pytest.skip('OS did not permit creating a symlink')
    with pytest.raises(OutsourceError):
        Store(board_root, create=True)
    assert target.read_bytes() == b'preserve'


@pytest.mark.parametrize('value', ['\\\\?\\C:\\source', '\\\\.\\C:\\source', '//?/C:/source', '//./C:/source'])
def test_windows_namespace_aliases_are_rejected(value):
    from tools.outsource.common import safe_absolute
    with pytest.raises(OutsourceError) as error:
        safe_absolute(value, must_exist=False)
    assert error.value.code == 'unsafe_path'


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows spelling aliases')
@pytest.mark.parametrize('value', ['C:/source./file', 'C:/source /file', 'C:/source/file:stream', '//host/share/source'])
def test_windows_ambiguous_spelling_is_rejected(value):
    from tools.outsource.common import safe_absolute
    with pytest.raises(OutsourceError):
        safe_absolute(value, must_exist=False)


def test_publish_expected_revision_is_honored(board):
    store, root = board
    publish(store, root)
    payload = store.events()[0]['payload']
    with pytest.raises(OutsourceError) as error:
        store.mutate('publish', 'next', 'owner', 'owner-session', 'publish-next', payload, expected_revision=9)
    assert error.value.code == 'revision_conflict'
    assert [job['ticket_id'] for job in store.list()] == ['job']


def test_integrated_rework_freezes_new_baseline_and_revokes_old_attempt(board, tmp_path):
    from tools.outsource import workspace
    store, root = board
    published = publish(store, root)
    change(store, 'claim')
    delivery, _ = delivery_for(store, root, tmp_path)
    submitted = change(store, 'submit', {'delivery': delivery})
    change(store, 'integration-start', actor='owner', session='owner-session')
    result = workspace.integrate(published['snapshot'], delivery, tmp_path / 'integration')
    change(store, 'integration-finish', {'result': result, 'delivery_digest': submitted['delivery_digest']}, actor='owner', session='owner-session')
    with pytest.raises(OutsourceError):
        change(store, 'rework', {'reason': 'needs another change', 'snapshot': published['snapshot']}, actor='owner', session='owner-session')
    current_snapshot = workspace.capture(published['contract']['projects'], tmp_path / 'rework-snapshot')
    revised = change(store, 'rework', {'reason': 'needs another change', 'snapshot': current_snapshot}, actor='owner', session='owner-session', request='rework-new')
    assert revised['state'] == 'claimed' and revised['attempt'] == 2
    assert revised['workspace'] is None and revised['delivery'] is None and revised['integration'] is None
    assert revised['snapshot']['manifest_sha256'] == current_snapshot['manifest_sha256']
    assert (root / 'src/a.txt').read_text(encoding='utf-8') == 'new'
    with pytest.raises(OutsourceError) as error:
        change(store, 'heartbeat')
    assert error.value.code == 'stale_attempt'
    bindings = workspace.materialize(current_snapshot, tmp_path / 'rework-execution')
    assert change(store, 'bind-workspace', {'workspace': bindings}, attempt=2, request='bind-rework')['state'] == 'running'
    assert len([event for event in store.events() if event['action'] == 'submit']) == 1


def test_frozen_manifest_reference_inline_fields_are_not_authoritative(board):
    from tools.outsource import workspace
    store, root = board
    contract = contract_for(root)
    snapshot = workspace.capture(contract['projects'], store.root / 'tickets' / 'job' / 'snapshot')
    snapshot['projects'] = []
    published = store.mutate('publish', 'job', 'owner', 'owner-session', 'publish-job', {'contract': contract, 'snapshot': snapshot})
    assert published['snapshot'] == {key: snapshot[key] for key in ('manifest_path', 'manifest_sha256')}
    assert workspace.verify_snapshot(published['snapshot'])['projects']


def test_worker_resources_cannot_expand_contract_scope(board):
    store, root = board
    publish(store, root)
    change(store, 'claim')
    for resource in ['device', 'browser/main']:
        with pytest.raises(OutsourceError):
            change(store, 'acquire', {'resources': [resource]}, request='bad-' + resource)
    assert change(store, 'acquire', {'resources': ['device/camera/stream']})['resources'][0]['resource'] == 'device/camera/stream'


def test_internal_resources_are_reserved_even_through_aliases(board):
    store, root = board
    publish(store, root)
    for action, payload in [
        ('acquire', {'resources': ['project/internal']}),
        ('register-resource', {'resource': 'checkout/internal', 'aliases': ['friendly']}),
        ('register-resource', {'resource': 'device/camera', 'aliases': ['project/internal']}),
    ]:
        with pytest.raises(OutsourceError):
            change(store, action, payload, actor='owner', session='owner-session', request=action + str(payload))
    with sqlite3.connect(store.db_path) as connection:
        connection.execute('INSERT INTO aliases VALUES(?,?)', ('friendly', 'project/internal'))
    with pytest.raises(OutsourceError):
        change(store, 'acquire', {'resources': ['friendly']}, actor='owner', session='owner-session', request='alias-lock')
