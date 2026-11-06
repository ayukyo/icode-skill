"""Transactional local collaboration. Participants must use the same owner-OS board.

Locks are cooperative and never expire automatically; this module is not a sandbox.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

from .common import OutsourceError, canonical_json, digest_json, safe_absolute, sha256_file


TERMINAL = {'accepted', 'cancelled'}
EXECUTION = {'bind-workspace', 'heartbeat', 'submit', 'confirm-cancel'}


def fail(code, message, **details):
    raise OutsourceError(code, message, details)


def overlap(first, second):
    first, second = first.rstrip('/'), second.rstrip('/')
    return first == second or first.startswith(second + '/') or second.startswith(first + '/')


def resource_name(value):
    if not isinstance(value, str) or not value or any(x in ('', '.', '..') for x in value.split('/')) or '\\' in value:
        fail('invalid_resource', 'Resource must be a nonempty slash-separated physical key')
    return value


def public_resource_name(value):
    value = resource_name(value)
    if value.split('/')[0] in {'project', 'checkout'}:
        fail('reserved_resource', 'project/ and checkout/ are internal coordination namespaces')
    return value


def frozen_ref(reference):
    """Keep immutable identity only; manifest body is reloaded when verified."""
    return {key: reference[key] for key in ('manifest_path', 'manifest_sha256')}


class Store:
    def __init__(self, root, create=False):
        self.root = safe_absolute(root, must_exist=not create)
        self.db_path = self.root / 'board.sqlite3'
        safe_absolute(self.db_path, must_exist=False)
        if create and not self.db_path.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            with closing(self._connect(write=True, validate=False)) as db:
                db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,ticket TEXT NOT NULL,action TEXT NOT NULL,actor TEXT NOT NULL,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(actor TEXT NOT NULL,key TEXT NOT NULL,digest TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(actor,key));
                CREATE TABLE IF NOT EXISTS locks(resource TEXT PRIMARY KEY,ticket TEXT NOT NULL,actor TEXT NOT NULL,session TEXT NOT NULL,attempt INTEGER NOT NULL,kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY,resource TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cursors(actor TEXT PRIMARY KEY,event_id INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries(event_id INTEGER NOT NULL,actor TEXT NOT NULL,PRIMARY KEY(event_id,actor));
                ''')
                db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', ('schema_version', '1'))
                db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', ('owner_os', platform.system()))
                db.commit()
        if not self.db_path.is_file():
            fail('board_missing', 'Existing board database is required')
        safe_absolute(self.db_path)
        with closing(self._connect()):
            pass

    def _connect(self, write=False, validate=True):
        connection = None
        try:
            safe_absolute(self.db_path, must_exist=validate)
            connection = sqlite3.connect(self.db_path if write else self.db_path.as_uri() + '?mode=ro', uri=not write, timeout=15)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            if not write:
                connection.execute('PRAGMA query_only=ON')
            if validate:
                metadata = dict(connection.execute('SELECT key,value FROM meta'))
                if metadata.get('schema_version') != '1':
                    fail('schema_mismatch', 'Unsupported board schema; no automatic migration is performed')
                if metadata.get('owner_os') != platform.system():
                    fail('owner_os_mismatch', 'Use the original owner OS CLI as a bridge; cross-kernel SQLite coordination is unsupported', owner_os=metadata.get('owner_os'), current_os=platform.system())
            return connection
        except (sqlite3.Error, OutsourceError) as exc:
            if connection is not None:
                connection.close()
            if isinstance(exc, OutsourceError):
                raise
            raise OutsourceError('board_invalid', str(exc)) from exc

    def resolve(self, path_or_id):
        value = str(path_or_id)
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value) and value not in ('.', '..', 'latest'):
            return value
        candidate = safe_absolute(value, must_exist=False)
        if candidate.name == 'request.md':
            candidate = candidate.parent
        if candidate.parent != self.root / 'tickets' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', candidate.name):
            fail('invalid_ticket', 'Ticket path must identify an exact directory in this board')
        return candidate.name

    def _get(self, db, ticket):
        row = db.execute('SELECT data FROM tickets WHERE id=?', (ticket,)).fetchone()
        if not row:
            fail('ticket_missing', 'Ticket does not exist', ticket_id=ticket)
        return json.loads(row['data'])

    def _decorate(self, db, result):
        result['resources'] = [dict(row) for row in db.execute('SELECT resource,actor,session,attempt,kind FROM locks WHERE ticket=? ORDER BY resource', (result['ticket_id'],))]
        return result

    def get(self, ticket):
        with closing(self._connect()) as db:
            return self._decorate(db, self._get(db, self.resolve(ticket)))

    def list(self, requester=None, state=None, parent_ref=None):
        with closing(self._connect()) as db:
            rows = [self._decorate(db, json.loads(row[0])) for row in db.execute('SELECT data FROM tickets ORDER BY id')]
        return [row for row in rows if (requester is None or row['requester'] == requester) and (state is None or row['state'] == state) and (parent_ref is None or row.get('parent_ref') == parent_ref)]

    def events(self, after=0, requester=None):
        if not isinstance(after, int) or after < 0:
            fail('invalid_cursor', 'Event cursor must be a nonnegative integer')
        with closing(self._connect()) as db:
            rows = [json.loads(row[0]) for row in db.execute('SELECT data FROM events WHERE id>? ORDER BY id', (after,))]
            if requester is not None:
                rows = [row for row in rows if self._get(db, row['ticket_id'])['requester'] == requester]
            return rows

    def inbox(self, actor, after=0):
        if not isinstance(after, int) or after < 0:
            fail('invalid_cursor', 'Event cursor must be a nonnegative integer')
        with closing(self._connect()) as db:
            cursor = db.execute('SELECT event_id FROM cursors WHERE actor=?', (actor,)).fetchone()
            after = max(after, cursor[0] if cursor else 0)
            return [json.loads(row[0]) for row in db.execute('SELECT e.data FROM events e JOIN deliveries d ON e.id=d.event_id WHERE d.actor=? AND e.id>? ORDER BY e.id', (actor, after))]

    def lookup_request(self, actor, request_id):
        with closing(self._connect()) as db:
            row = db.execute('SELECT result FROM requests WHERE actor=? AND key=?', (actor, request_id)).fetchone()
            return json.loads(row[0]) if row else None

    def request_envelope(self, actor, request_id):
        """Read the original command identity before a CLI repeats file work."""
        with closing(self._connect()) as db:
            row = db.execute('SELECT digest,result FROM requests WHERE actor=? AND key=?', (actor, request_id)).fetchone()
            if row is None:
                return None
            result = json.loads(row['result'])
            event = json.loads(db.execute('SELECT data FROM events WHERE id=?', (result['event_id'],)).fetchone()[0])
            return {'action': event['action'], 'ticket': event['ticket_id'], 'session': event['session'], 'payload_digest': digest_json(event['payload']), 'request_digest': row['digest'], 'result': result}

    def doctor(self):
        """Check SQLite and frozen references without repairing or releasing locks."""
        from . import workspace
        issues = []
        with closing(self._connect()) as db:
            integrity = [row[0] for row in db.execute('PRAGMA integrity_check')]
            resources = [dict(row) for row in db.execute('SELECT * FROM locks ORDER BY resource')]
            for row in db.execute('SELECT data FROM tickets'):
                job = json.loads(row[0])
                try:
                    self._contract(job['contract'], job['requester'])
                    if digest_json(job['contract']) != job['contract_digest']:
                        fail('contract_changed', 'Stored contract digest differs')
                    for key, verify in (('snapshot', workspace.verify_snapshot), ('delivery', workspace.verify_delivery)):
                        if job.get(key):
                            self._reference(job[key])
                            verify(job[key])
                    if job.get('integration'):
                        self._reference(job['integration'])
                        workspace.verify_integration(job['integration'], job['delivery'])
                except (OutsourceError, OSError, KeyError, TypeError, ValueError) as exc:
                    issues.append({'ticket_id': job['ticket_id'], 'error': str(exc), 'error_code': getattr(exc, 'code', 'invalid_reference')})
        return {'ok': integrity == ['ok'] and not issues, 'integrity': integrity, 'issues': issues, 'resources': resources}

    def _requester(self, job, actor, session):
        if actor != job['requester'] or session != job['requester_session']:
            fail('forbidden', 'Action requires the bound requester session')

    def _worker(self, job, actor, session, attempt):
        if attempt != job['attempt']:
            fail('stale_attempt', 'Execution attempt is no longer current')
        if actor != job['claimant'] or session != job['claim_session']:
            fail('forbidden', 'Action requires the bound execution session')

    def _participant(self, job, actor, session, attempt):
        if actor == job['requester']:
            self._requester(job, actor, session)
        else:
            self._worker(job, actor, session, attempt)

    def _no_locks(self, db, job):
        if db.execute('SELECT 1 FROM locks WHERE ticket=?', (job['ticket_id'],)).fetchone():
            fail('resources_held', 'Release resources after confirming operations have stopped')

    def _contract(self, contract, actor):
        required = ('title', 'goal', 'requester', 'projects', 'acceptance', 'constraints')
        if not isinstance(contract, dict) or type(contract.get('schema_version')) is not int or contract.get('schema_version') != 1 or any(key not in contract for key in required):
            fail('invalid_contract', 'Contract schema_version=1 and required fields are required')
        if set(contract) - {'schema_version', *required, 'resources', 'depends_on', 'parent_ref'}:
            fail('invalid_contract', 'Unknown contract fields are forbidden')
        if any(not isinstance(contract[key], str) or not contract[key].strip() for key in ('title', 'goal', 'requester')) or contract['requester'] != actor:
            fail('invalid_contract', 'Contract requester must match publisher')
        if not isinstance(contract['constraints'], list) or not isinstance(contract['projects'], list) or not contract['projects']:
            fail('invalid_contract', 'Constraints and projects must be arrays')
        if any(not isinstance(item, str) or not item.strip() for item in contract['constraints']):
            fail('invalid_contract', 'Constraints must contain nonempty strings')
        acceptance = contract['acceptance']
        if not isinstance(acceptance, list) or not acceptance or any(not isinstance(x, dict) or not isinstance(x.get('id'), str) or not x['id'] or not isinstance(x.get('check'), str) or not x['check'] for x in acceptance):
            fail('invalid_contract', 'Acceptance requires nonempty id/check entries')
        if len({x['id'] for x in acceptance}) != len(acceptance):
            fail('invalid_contract', 'Acceptance IDs must be unique')
        if any(set(item) != {'id', 'check'} for item in acceptance):
            fail('invalid_contract', 'Unknown acceptance fields are forbidden')
        read_count = 0
        for project in contract['projects']:
            if not isinstance(project, dict) or any(k not in project for k in ('root', 'read_paths', 'write_paths')):
                fail('invalid_contract', 'Project root/read_paths/write_paths are required')
            if set(project) != {'root', 'read_paths', 'write_paths'}:
                fail('invalid_contract', 'Unknown project fields are forbidden')
            if not isinstance(project['root'], str) or not project['root'].strip():
                fail('invalid_contract', 'Project root must be an absolute path string')
            root = safe_absolute(project['root'])
            if not root.is_dir() or any(not isinstance(project[k], list) for k in ('read_paths', 'write_paths')):
                fail('invalid_contract', 'Project root must be a directory and scopes arrays')
            for key in ('read_paths', 'write_paths'):
                for path in project[key]:
                    if not isinstance(path, str) or not path or Path(path).is_absolute() or '..' in Path(path).parts:
                        fail('invalid_contract', 'Scopes must be anchored relative paths')
                    safe_absolute(root / path, must_exist=False)
            for write in project['write_paths']:
                if not any(read == '.' or read == write or write.replace('\\', '/').startswith(read.replace('\\', '/').rstrip('/') + '/') for read in project['read_paths']):
                    fail('invalid_contract', 'Every write scope must be contained in a read scope')
            read_count += len(project['read_paths'])
        if not read_count:
            fail('invalid_contract', 'At least one read scope is required')
        for key in ('depends_on', 'resources'):
            if not isinstance(contract.get(key, []), list) or any(not isinstance(x, str) or not x for x in contract.get(key, [])):
                fail('invalid_contract', key + ' must be a string array')
            if len(set(contract.get(key, []))) != len(contract.get(key, [])):
                fail('invalid_contract', key + ' must contain unique values')
        for resource in contract.get('resources', []):
            public_resource_name(resource)
        if 'parent_ref' in contract and not isinstance(contract['parent_ref'], str):
            fail('invalid_contract', 'parent_ref must be a string')

    def _project_key(self, root):
        root = safe_absolute(root)
        result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--path-format=absolute', '--git-common-dir'], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=10, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
        return os.path.normcase(str(Path(result.stdout.strip()).resolve())) if result.returncode == 0 else os.path.normcase(str(root))

    def _scopes(self, job):
        if 'reservation_scopes' in job:
            return job['reservation_scopes']
        scopes = []
        for project in job['contract']['projects']:
            key = self._project_key(project['root'])
            prefix_result = subprocess.run(['git', '-C', project['root'], 'rev-parse', '--show-prefix'], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=10, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
            for path in project['write_paths']:
                if prefix_result.returncode == 0:
                    scoped = prefix_result.stdout.strip() + (path if path != '.' else '')
                    scopes.append([key, os.path.normcase(scoped.rstrip('/')).replace('\\', '/')])
                else:
                    # Non-Git nested roots still share the actual filesystem.
                    scopes.append(['filesystem', os.path.normcase(str(safe_absolute(Path(project['root']) / path, must_exist=False))).replace('\\', '/')])
        return scopes

    def _check_integration_reservation(self, db, job):
        roots = [os.path.normcase(str(safe_absolute(p['root']))).replace('\\', '/') for p in job['contract']['projects']]
        for row in db.execute("SELECT DISTINCT ticket FROM locks WHERE kind='integration' AND ticket<>?", (job['ticket_id'],)):
            other = self._get(db, row['ticket'])
            other_roots = [os.path.normcase(str(safe_absolute(p['root']))).replace('\\', '/') for p in other['contract']['projects']]
            if set(job['project_keys']) & set(other['project_keys']) or any(overlap(first, second) for first in roots for second in other_roots):
                fail('scope_conflict', 'Target project is exclusively reserved by an active integration', conflicting_ticket=other['ticket_id'])

    def _check_scope(self, db, job):
        scopes = self._scopes(job)
        self._check_integration_reservation(db, job)
        for row in db.execute('SELECT data FROM tickets WHERE id<>?', (job['ticket_id'],)):
            other = json.loads(row[0])
            if other['state'] in TERMINAL or not other['claimant']:
                continue
            if any(key == other_key and (not path or not other_path or overlap(path, other_path)) for key, path in scopes for other_key, other_path in self._scopes(other)):
                fail('scope_conflict', 'Another live ticket reserves an overlapping write scope', conflicting_ticket=other['ticket_id'])

    def _canonical_resource(self, db, resource):
        resource = resource_name(resource)
        # An alias may identify a parent: cam/stream maps to device/camera/stream.
        for row in db.execute('SELECT alias,resource FROM aliases ORDER BY length(alias) DESC'):
            if resource == row['alias'] or resource.startswith(row['alias'] + '/'):
                return row['resource'] + resource[len(row['alias']):]
        return resource

    def _acquire(self, db, job, actor, session, attempt, resources, kind='resource'):
        if not isinstance(resources, list) or not resources:
            fail('invalid_resource', 'A nonempty resource set is required')
        wanted = {self._canonical_resource(db, public_resource_name(value) if kind == 'resource' else value) for value in resources}
        if kind == 'resource':
            for resource in wanted:
                public_resource_name(resource)
            if actor != job['requester']:
                declared = [self._canonical_resource(db, resource) for resource in job['contract'].get('resources', [])]
                if any(not any(resource == scope or resource.startswith(scope + '/') for scope in declared) for resource in wanted):
                    fail('resource_out_of_scope', 'Execution resources must be declared in the contract or be children of declared resources')
        for row in db.execute('SELECT * FROM locks'):
            if any(overlap(resource, row['resource']) for resource in wanted) and not (row['ticket'] == job['ticket_id'] and row['actor'] == actor and row['session'] == session and row['attempt'] == attempt and row['kind'] == kind):
                fail('resource_conflict', 'Resource is already exclusively held', resource=row['resource'], conflicting_ticket=row['ticket'])
        for resource in wanted:
            db.execute('INSERT OR IGNORE INTO locks VALUES(?,?,?,?,?,?)', (resource, job['ticket_id'], actor, session, attempt, kind))

    def _reference(self, reference):
        if not isinstance(reference, dict) or not reference.get('manifest_path') or not reference.get('manifest_sha256'):
            fail('invalid_reference', 'A frozen manifest_path and manifest_sha256 are required')
        path = safe_absolute(reference['manifest_path'])
        if sha256_file(path) != reference['manifest_sha256']:
            fail('reference_changed', 'Frozen manifest digest does not match')

    def mutate(self, action, ticket, actor, session, request_id, payload, expected_revision=None, attempt=None):
        if not all(isinstance(x, str) and x.strip() for x in (action, actor, session, request_id)) or not isinstance(payload, dict):
            fail('invalid_request', 'Action, actor, session, request_id and object payload are required')
        ticket = self.resolve(ticket)
        try:
            request_digest = digest_json([action, ticket, session, payload, expected_revision, attempt])
        except (TypeError, ValueError) as exc:
            fail('invalid_request', str(exc))
        with closing(self._connect(write=True)) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                receipt = db.execute('SELECT digest,result FROM requests WHERE actor=? AND key=?', (actor, request_id)).fetchone()
                if receipt:
                    if receipt['digest'] != request_digest:
                        fail('request_conflict', 'request_id was already used for a different request')
                    db.rollback()
                    return json.loads(receipt['result'])
                if action == 'publish':
                    if expected_revision is not None and expected_revision != 0:
                        fail('revision_conflict', 'A new ticket requires expected_revision=0')
                    job = self._publish(db, ticket, actor, session, payload)
                else:
                    job = self._get(db, ticket)
                    if expected_revision is not None and expected_revision != job['revision']:
                        fail('revision_conflict', 'Ticket revision changed', actual_revision=job['revision'])
                    self._apply(db, job, action, actor, session, attempt, payload)
                    job['revision'] += 1
                    job['updated_at'] = time.time()
                event = {'ticket_id': ticket, 'action': action, 'actor': actor, 'session': session, 'attempt': job['attempt'], 'revision': job['revision'], 'state': job['state'], 'payload': payload, 'timestamp': time.time()}
                event_id = db.execute('INSERT INTO events(ticket,action,actor,data) VALUES(?,?,?,?)', (ticket, action, actor, '{}')).lastrowid
                event['event_id'] = event_id
                db.execute('UPDATE events SET data=? WHERE id=?', (canonical_json(event), event_id))
                recipients = {job['requester'], job['claimant']} - {None, actor}
                if action in {'ack', 'heartbeat'}:
                    recipients = set()
                if action == 'message' and payload.get('to'):
                    if payload['to'] not in recipients:
                        fail('forbidden', 'Message recipient must be another participant')
                    recipients = {payload['to']}
                for recipient in recipients:
                    db.execute('INSERT INTO deliveries VALUES(?,?)', (event_id, recipient))
                db.execute('INSERT OR REPLACE INTO tickets VALUES(?,?)', (ticket, canonical_json(job)))
                result = self._decorate(db, dict(job))
                result['event_id'] = event_id
                db.execute('INSERT INTO requests VALUES(?,?,?,?)', (actor, request_id, request_digest, canonical_json(result)))
                db.commit()
                return result
            except OutsourceError:
                db.rollback()
                raise
            except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
                db.rollback()
                raise OutsourceError('operation_failed', str(exc)) from exc

    def _publish(self, db, ticket, actor, session, payload):
        from . import workspace
        if db.execute('SELECT 1 FROM tickets WHERE id=?', (ticket,)).fetchone():
            fail('ticket_exists', 'Ticket already exists')
        contract = normalize_contract(payload.get('contract'), actor)
        snapshot = payload.get('snapshot')
        self._reference(snapshot)
        manifest = workspace.verify_snapshot(snapshot)
        if [{key: project[key] for key in ('root', 'read_paths', 'write_paths')} for project in manifest['projects']] != [{key: project[key] for key in ('root', 'read_paths', 'write_paths')} for project in contract['projects']]:
            fail('contract_changed', 'Snapshot projects differ from the contract')
        snapshot = frozen_ref(snapshot)
        for dependency in contract.get('depends_on', []):
            if dependency == ticket:
                fail('invalid_contract', 'A ticket cannot depend on itself')
            self._get(db, self.resolve(dependency))
        job = {'ticket_id': ticket, 'revision': 1, 'state': 'published', 'attempt': 0, 'contract': contract, 'contract_digest': digest_json(contract), 'snapshot': snapshot, 'workspace': None, 'delivery': None, 'integration': None, 'claimant': None, 'claim_session': None, 'requester': actor, 'requester_session': session, 'parent_ref': contract.get('parent_ref'), 'cancel_requested': False, 'created_at': time.time(), 'updated_at': time.time()}
        job['reservation_scopes'] = self._scopes(job)
        job['project_keys'] = [self._project_key(p['root']) for p in contract['projects']]
        return job

    def _apply(self, db, job, action, actor, session, attempt, payload):
        from . import workspace
        if action == 'claim':
            if job['state'] != 'published' or job['claimant']:
                fail('already_claimed', 'Ticket is not available for claiming')
            if actor == job['requester']:
                fail('forbidden', 'Requester cannot claim its own ticket')
            for dependency in job['contract'].get('depends_on', []):
                if self._get(db, dependency)['state'] != 'accepted':
                    fail('dependency_blocked', 'A dependency has not been accepted', dependency=dependency)
            self._reference(job['snapshot'])
            workspace.verify_snapshot(job['snapshot'])
            self._check_scope(db, job)
            job.update(claimant=actor, claim_session=session, attempt=job['attempt'] + 1, state='claimed')
            return
        if action == 'requester-resume':
            if actor != job['requester'] or payload.get('previous_session') != job['requester_session'] or payload.get('stopped') is not True or not isinstance(payload.get('reason'), str) or not payload['reason'].strip():
                fail('forbidden', 'Resume requires the previous requester session and explicit stopped confirmation')
            held = db.execute('SELECT * FROM locks WHERE ticket=? AND actor=?', (job['ticket_id'], actor)).fetchall()
            if any(row['kind'] != 'integration' for row in held):
                fail('resources_held', 'Release ordinary requester resources before resuming')
            db.execute("UPDATE locks SET session=? WHERE ticket=? AND actor=? AND kind='integration'", (session, job['ticket_id'], actor))
            job['requester_session'] = session
            return
        if action == 'confirm-cancel' and job['claimant'] is None:
            self._requester(job, actor, session)
        elif action in EXECUTION:
            self._worker(job, actor, session, attempt)
        elif action in {'acquire', 'release', 'message', 'ack'}:
            self._participant(job, actor, session, attempt)
        else:
            self._requester(job, actor, session)
        if job['state'] in TERMINAL and action not in {'message', 'ack'}:
            fail('invalid_state', 'Ticket is terminal')
        if action == 'bind-workspace':
            if job['state'] not in {'claimed', 'running'} or job['workspace']:
                fail('invalid_state', 'Bind requires an unbound claimed ticket')
            bindings = payload.get('workspace')
            self._check_scope(db, job)
            workspace.validate_workspace(job['snapshot'], bindings)
            if not isinstance(bindings, dict):
                fail('invalid_workspace', 'Workspace bindings must be an object')
            for source, target in bindings.items():
                target = safe_absolute(target)
                # Any real checkout is exclusive, including non-overlapping scopes.
                if target == safe_absolute(source) and next(p for p in job['contract']['projects'] if p['root'] == source)['write_paths']:
                    self._acquire(db, job, actor, session, attempt, ['checkout/' + digest_json(str(target))], kind='workspace')
                for row in db.execute('SELECT data FROM tickets WHERE id<>?', (job['ticket_id'],)):
                    other = json.loads(row[0])
                    if other['state'] not in TERMINAL and other.get('workspace') and any(overlap(os.path.normcase(str(target)).replace('\\', '/'), os.path.normcase(str(safe_absolute(value))).replace('\\', '/')) for value in other['workspace'].values()):
                        fail('workspace_conflict', 'An execution directory is already bound')
            job.update(workspace=bindings, state='running')
        elif action == 'heartbeat':
            if job['state'] not in {'claimed', 'running', 'cancel_requested'}:
                fail('invalid_state', 'No active execution')
            job['heartbeat_at'] = time.time()
        elif action == 'message':
            if not isinstance(payload.get('body'), str) or not payload['body'].strip():
                fail('invalid_message', 'A nonempty message body is required')
        elif action == 'ack':
            event_id = payload.get('event_id')
            if not isinstance(event_id, int) or not db.execute('SELECT 1 FROM deliveries WHERE event_id=? AND actor=?', (event_id, actor)).fetchone():
                fail('invalid_cursor', 'Cursor must identify an event delivered to this actor')
            db.execute('INSERT INTO cursors VALUES(?,?) ON CONFLICT(actor) DO UPDATE SET event_id=max(event_id,excluded.event_id)', (actor, event_id))
        elif action == 'register-resource':
            resource = self._canonical_resource(db, public_resource_name(payload.get('resource')))
            public_resource_name(resource)
            aliases = payload.get('aliases')
            if not isinstance(aliases, list):
                fail('invalid_resource', 'aliases must be an array')
            if db.execute('SELECT 1 FROM locks').fetchone():
                fail('resources_held', 'Resource aliases may only be registered while no resources are held')
            for alias in aliases:
                public_resource_name(alias)
                public_resource_name(self._canonical_resource(db, alias))
                if alias == resource or overlap(alias, resource):
                    fail('invalid_resource', 'Alias must not overlap its physical key')
                for row in db.execute('SELECT alias,resource FROM aliases'):
                    if overlap(alias, row['alias']) or overlap(alias, row['resource']) or overlap(resource, row['alias']):
                        fail('resource_alias_conflict', 'Resource alias conflicts with an existing mapping')
                db.execute('INSERT INTO aliases VALUES(?,?)', (alias, resource))
        elif action == 'acquire':
            if job['cancel_requested']:
                fail('invalid_state', 'Cancellation has been requested')
            self._acquire(db, job, actor, session, job['attempt'] if actor != job['requester'] else 0, payload.get('resources'))
        elif action == 'release':
            if not isinstance(payload.get('reason'), str) or not payload['reason'].strip():
                fail('stop_confirmation_required', 'Explain that the resource operation has ended')
            resources = payload.get('resources')
            if not isinstance(resources, list) or not resources:
                fail('invalid_resource', 'A nonempty release set is required')
            for resource in resources:
                resource = self._canonical_resource(db, resource)
                row = db.execute('SELECT * FROM locks WHERE resource=?', (resource,)).fetchone()
                if not row or row['ticket'] != job['ticket_id'] or row['actor'] != actor or row['session'] != session or row['kind'] != 'resource':
                    fail('forbidden', 'Only the owning session can release a held resource')
                db.execute('DELETE FROM locks WHERE resource=?', (resource,))
        elif action == 'submit':
            if job['state'] != 'running' or job['cancel_requested']:
                fail('invalid_state', 'Submit requires an active bound workspace')
            self._no_locks(db, job)
            delivery = payload.get('delivery')
            self._reference(delivery)
            manifest = workspace.verify_delivery(delivery)
            workspace.validate_delivery_workspace(job['snapshot'], job['workspace'], delivery)
            # Match the exact frozen snapshot, not an untrusted verified flag.
            snapshot_digest = job['snapshot']['manifest_sha256']
            delivery_snapshot = manifest.get('snapshot_sha256') or manifest.get('snapshot_digest') or manifest.get('snapshot', {}).get('manifest_sha256')
            if delivery_snapshot != snapshot_digest:
                fail('delivery_mismatch', 'Delivery does not bind the published snapshot')
            job.update(delivery=frozen_ref(delivery), delivery_digest=manifest['manifest_sha256'], state='submitted', integration=None)
        elif action == 'request-cancel':
            if job['state'] in {'integrating', 'integration_failed', 'integrated'}:
                fail('invalid_state', 'Resolve integration before cancelling')
            job.update(cancel_requested=True, state='cancel_requested')
        elif action == 'confirm-cancel':
            if not job['cancel_requested'] or payload.get('stopped') is not True or not payload.get('reason'):
                fail('stop_confirmation_required', 'Cancellation requires an explicit stopped confirmation')
            self._no_locks(db, job)
            job.update(state='cancelled')
        elif action == 'handoff':
            if job['state'] not in {'claimed', 'running', 'cancel_requested'} or payload.get('stopped') is not True or not payload.get('reason'):
                fail('stop_confirmation_required', 'Handoff requires explicit confirmation that old execution stopped')
            self._no_locks(db, job)
            if not all(isinstance(payload.get(k), str) and payload[k].strip() for k in ('to_actor', 'to_session')) or payload['to_actor'] == job['requester']:
                fail('invalid_request', 'Handoff requires a distinct execution actor and session')
            job.update(claimant=payload['to_actor'], claim_session=payload['to_session'], attempt=job['attempt'] + 1, workspace=None, delivery=None, integration=None, cancel_requested=False, state='claimed')
        elif action == 'integration-start':
            if job['state'] not in {'submitted', 'integration_failed', 'integrating'}:
                fail('invalid_state', 'Integration requires a submitted delivery')
            self._reference(job['delivery'])
            workspace.verify_delivery(job['delivery'])
            self._check_integration_reservation(db, job)
            resources = ['project/' + digest_json(key) for key in job['project_keys']]
            # Execution is in independent materialized directories. Target locks
            # serialize integrations without deadlocking two submitted tickets.
            self._acquire(db, job, actor, session, 0, resources, kind='integration')
            job.update(state='integrating')
        elif action == 'integration-finish':
            if job['state'] != 'integrating' or payload.get('delivery_digest') != job['delivery_digest']:
                fail('invalid_state', 'Finish must bind the active integration delivery')
            result = payload.get('result')
            self._reference(result)
            verified = workspace.verify_integration(result, job['delivery'])
            if not isinstance(verified, dict):
                fail('invalid_reference', 'Integration verification did not return a receipt')
            completed = verified.get('state') == 'completed' or verified.get('status') == 'completed' or verified.get('ok') is True
            job.update(integration=frozen_ref(result), state='integrated' if completed else 'integration_failed')
            # Failed/unknown operations retain locks until a verified finish.
            if completed:
                db.execute("DELETE FROM locks WHERE ticket=? AND kind='integration'", (job['ticket_id'],))
        elif action == 'accept':
            if not job['delivery'] or payload.get('delivery_digest') != job['delivery_digest']:
                fail('acceptance_failed', 'Acceptance must bind the current delivery digest')
            self._reference(job['delivery'])
            manifest = workspace.verify_delivery(job['delivery'])
            no_code = payload.get('no_code') is True and manifest.get('changes') == []
            if not no_code:
                if job['state'] != 'integrated' or not job['integration']:
                    fail('acceptance_failed', 'Code delivery requires completed verified integration')
                self._reference(job['integration'])
                workspace.verify_integration(job['integration'], job['delivery'])
            checks = payload.get('checks')
            if not isinstance(checks, list) or len(checks) != len(job['contract']['acceptance']) or {x.get('id') for x in checks if isinstance(x, dict)} != {x['id'] for x in job['contract']['acceptance']}:
                fail('acceptance_failed', 'Every acceptance ID needs a passing evidence entry')
            for check in checks:
                evidence = check.get('evidence')
                if check.get('pass') is not True or not isinstance(evidence, dict) or not evidence.get('path') or not evidence.get('sha256'):
                    fail('acceptance_failed', 'Each passing check requires a frozen evidence file reference')
                if sha256_file(safe_absolute(evidence['path'])) != evidence['sha256']:
                    fail('acceptance_failed', 'Acceptance evidence digest changed')
            self._no_locks(db, job)
            job.update(state='accepted', acceptance=checks)
        elif action == 'rework':
            if job['state'] not in {'submitted', 'integrated'} or not payload.get('reason'):
                fail('invalid_state', 'Rework requires reviewed delivery and a reason')
            self._no_locks(db, job)
            if job['state'] == 'integrated':
                # Already-applied bytes become the next execution baseline. The
                # old receipt and every previous frozen object remain in events.
                self._check_integration_reservation(db, job)
                self._reference(job['integration'])
                workspace.verify_integration(job['integration'], job['delivery'])
                snapshot = payload.get('snapshot')
                self._reference(snapshot)
                manifest = workspace.verify_snapshot_current(snapshot)
                if [{key: project[key] for key in ('root', 'read_paths', 'write_paths')} for project in manifest['projects']] != job['contract']['projects']:
                    fail('contract_changed', 'Rework snapshot projects differ from the frozen contract')
                job.update(snapshot=frozen_ref(snapshot), attempt=job['attempt'] + 1, state='claimed', workspace=None)
            else:
                if 'snapshot' in payload:
                    fail('invalid_request', 'Submitted rework retains its original snapshot')
                job['state'] = 'running'
            job.update(integration=None, delivery=None, delivery_digest=None)
        else:
            fail('unknown_action', 'Unknown mutation action', action=action)


def validate_contract(contract, actor=None):
    """Validate before capture so invalid contracts cannot trigger source reads."""
    publisher = actor or (contract.get('requester') if isinstance(contract, dict) else None)
    Store._contract(None, contract, publisher)


def normalize_contract(contract, actor=None):
    validate_contract(contract, actor)
    normalized = json.loads(canonical_json(contract))
    for project in normalized['projects']:
        project['root'] = str(safe_absolute(project['root']))
        for key in ('read_paths', 'write_paths'):
            project[key] = [path.replace('\\', '/') for path in project[key]]
    return normalized
