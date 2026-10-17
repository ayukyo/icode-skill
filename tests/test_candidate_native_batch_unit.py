"""Native candidate contracts; Git mutations are confined to disposable fixtures."""
import hashlib
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_batch_candidate', ROOT / 'tools/icode_candidate.py')
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


class NativeCandidateBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='icode-native-batch-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_ATTR_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0')
        self.git('init', '-q')
        self.git('config', 'core.autocrlf', 'false')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Native Fixture')

    def git(self, *args, input_data=None):
        result = subprocess.run(['git', '--no-optional-locks', '-c', 'core.hooksPath=' + os.devnull,
            '-c', 'core.fsmonitor=false', '-c', 'commit.gpgSign=false', '-c', 'gc.auto=0',
            '-C', str(self.repo), *args], input=input_data, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=15, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def commit_fixture(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD').decode().strip()

    def capture(self, base, **kwargs):
        return api.capture_candidate(self.repo, base=base, **kwargs)

    def test_many_crlf_files_complete_with_bounded_object_processes(self):
        count = 1000
        for number in range(count):
            (self.repo / ('file_%04d.txt' % number)).write_bytes(('value = %d\n' % number).encode())
        base = self.commit_fixture()
        for number in range(count):
            (self.repo / ('file_%04d.txt' % number)).write_bytes(('value = %d\r\n' % number).encode())
        index_before = (self.repo / '.git/index').read_bytes()
        with patch.object(subprocess, 'Popen', wraps=subprocess.Popen) as calls:
            began = time.monotonic()
            snapshot = self.capture(base)
            elapsed = time.monotonic() - began
        object_reads = [call for call in calls.call_args_list if 'cat-file' in call.args[0]]
        print(json.dumps({'native_capture_seconds': elapsed, 'files': count,
                          'git_calls': len(calls.call_args_list), 'object_processes': len(object_reads)}))
        self.assertEqual(len(snapshot['manifest']), count)
        self.assertTrue(api.snapshot_identity_ok(snapshot))
        self.assertLessEqual(len(object_reads), 2, 'ordinary small objects must use bounded batch reads')
        self.assertEqual(index_before, (self.repo / '.git/index').read_bytes())
        for row in snapshot['manifest']:
            self.assertEqual(row['sha256'], hashlib.sha256((self.repo / row['path']).read_bytes()).hexdigest())

    def test_lfs_staged_and_normal_bytes_keep_their_meaning(self):
        data = b'\x00hydrated-content'
        pointer = (b'version https://git-lfs.github.com/spec/v1\noid sha256:'
                   + hashlib.sha256(data).hexdigest().encode() + b'\nsize '
                   + str(len(data)).encode() + b'\n')
        (self.repo / 'lfs.bin').write_bytes(pointer)
        (self.repo / 'other.bin').write_bytes(pointer)
        (self.repo / 'ordinary.txt').write_bytes(b'plain\n')
        (self.repo / 'staged.txt').write_bytes(b'old\n')
        (self.repo / 'large.txt').write_bytes(b'large\n' * 1000)
        base = self.commit_fixture()
        pointer_oid = self.git('rev-parse', 'HEAD:lfs.bin').decode().strip()
        (self.repo / 'lfs.bin').write_bytes(data)
        (self.repo / 'other.bin').write_bytes(data + b'not-matching')
        (self.repo / 'ordinary.txt').write_bytes(b'plain\r\n')
        (self.repo / 'large.txt').write_bytes(b'large\r\n' * 1000)
        (self.repo / 'staged.txt').write_bytes(b'new\n')
        self.git('add', 'staged.txt')
        (self.repo / 'staged.txt').write_bytes(b'new\r\n')
        before = (self.repo / '.git/index').read_bytes()
        rows = {row['path']: row for row in self.capture(base)['manifest']}
        self.assertEqual(rows['lfs.bin']['git_oid'], pointer_oid)
        self.assertEqual(rows['lfs.bin']['sha256'], hashlib.sha256(data).hexdigest())
        for name in ('other.bin', 'ordinary.txt', 'staged.txt', 'large.txt'):
            content = (self.repo / name).read_bytes()
            expected = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
            self.assertEqual(rows[name]['git_oid'], expected)
        self.assertEqual(before, (self.repo / '.git/index').read_bytes())

    def test_unchanged_files_do_not_start_object_processes(self):
        (self.repo / 'source.py').write_bytes(b'value = 1\n')
        base = self.commit_fixture()
        with patch.object(subprocess, 'Popen', wraps=subprocess.Popen) as calls:
            self.capture(base)
        self.assertFalse(any('cat-file' in call.args[0] for call in calls.call_args_list))

    def changed_fixture(self):
        (self.repo / 'a').write_bytes(b'plain\n')
        base = self.commit_fixture()
        (self.repo / 'a').write_bytes(b'plain\r\n')
        return base

    def test_small_manifest_budget_keeps_legacy_lfs_content_limit(self):
        data = b'ok'
        pointer = (b'version https://git-lfs.github.com/spec/v1\noid sha256:'
            + hashlib.sha256(data).hexdigest().encode() + b'\nsize 2\n')
        (self.repo / 'a').write_bytes(pointer)
        base = self.commit_fixture()
        (self.repo / 'a').write_bytes(data)
        ordinary = self.capture(base)
        tight = self.capture(base, limits={'max_git_bytes': 56})
        self.assertEqual(ordinary, tight)
        self.assertEqual(tight['manifest'][0]['git_oid'], self.git('rev-parse', 'HEAD:a').decode().strip())

    def test_small_budget_uses_batches_for_many_small_objects(self):
        for number in range(100):
            (self.repo / ('f%03d' % number)).write_bytes(('%d\n' % number).encode())
        base = self.commit_fixture()
        for number in range(100):
            (self.repo / ('f%03d' % number)).write_bytes(('%d\r\n' % number).encode())
        with patch.object(subprocess, 'Popen', wraps=subprocess.Popen) as calls:
            result = self.capture(base, limits={'max_git_bytes': 8192})
        self.assertEqual(len(result['manifest']), 100)
        self.assertLessEqual(sum('cat-file' in call.args[0] for call in calls.call_args_list), 6)

    def test_missing_index_object_is_not_treated_as_non_lfs(self):
        base = self.changed_fixture()
        self.git('update-index', '--cacheinfo', '100644,' + '1' * 40 + ',a')
        with self.assertRaises(api.CandidateError):
            self.capture(base)

    def test_wrong_index_object_type_is_rejected(self):
        base = self.changed_fixture()
        self.git('update-index', '--cacheinfo', '100644,' + base + ',a')
        with self.assertRaises(api.CandidateError):
            self.capture(base)

    def test_malformed_metadata_never_falls_back(self):
        base = self.changed_fixture()
        native = api._git
        faults = {
            'missing': lambda raw: raw.split(b' ')[0] + b' missing\n',
            'wrong_oid': lambda raw: b'0' * 40 + raw[40:],
            'wrong_type': lambda raw: raw.replace(b' blob ', b' tree '),
            'negative_size': lambda raw: raw.rsplit(b' ', 1)[0] + b' -1\n',
            'oversized_size': lambda raw: raw.rsplit(b' ', 1)[0] + b' 999999999999999999999\n',
            'noncanonical_size': lambda raw: raw.rsplit(b' ', 1)[0] + b' 06\n',
            'unterminated': lambda raw: raw[:-1],
            'duplicate': lambda raw: raw + raw,
        }
        for name, mutate in faults.items():
            with self.subTest(fault=name):
                observed = []

                def reader(root, args, limit, **kwargs):
                    observed.append(args)
                    raw = native(root, args, limit, **kwargs)
                    return mutate(raw) if args == ['cat-file', '--batch-check'] else raw

                with patch.object(api, '_git', side_effect=reader):
                    with self.assertRaises(api.CandidateError):
                        self.capture(base)
                self.assertFalse(any(args[:2] == ['cat-file', 'blob'] for args in observed))

    def test_malformed_content_never_falls_back(self):
        base = self.changed_fixture()
        native = api._git
        faults = {
            'truncated': lambda raw: raw[:-1],
            'extra': lambda raw: raw + b'extra',
            'wrong_oid': lambda raw: b'0' * 40 + raw[40:],
            'wrong_size': lambda raw: raw.replace(b' blob 6\n', b' blob 7\n', 1),
            'wrong_content': lambda raw: raw.replace(b'plain\n', b'other\n', 1),
        }
        for name, mutate in faults.items():
            with self.subTest(fault=name):
                observed = []

                def reader(root, args, limit, **kwargs):
                    observed.append(args)
                    raw = native(root, args, limit, **kwargs)
                    return mutate(raw) if args == ['cat-file', '--batch'] else raw

                with patch.object(api, '_git', side_effect=reader):
                    with self.assertRaises(api.CandidateError):
                        self.capture(base)
                self.assertFalse(any(args[:2] == ['cat-file', 'blob'] for args in observed))

    def test_index_drift_during_batch_is_rejected(self):
        base = self.changed_fixture()
        native = api._git

        def reader(root, args, limit, **kwargs):
            raw = native(root, args, limit, **kwargs)
            if args == ['cat-file', '--batch-check']:
                self.git('add', 'a')
            return raw

        with patch.object(api, '_git', side_effect=reader):
            with self.assertRaises(api.CandidateError):
                self.capture(base)

    def test_file_change_while_hashing_is_rejected(self):
        base = self.changed_fixture()
        native_open = os.fdopen
        target = self.repo / 'a'
        target_stat = target.stat()
        changed = [False]

        class ObservedStream:
            def __init__(self, stream):
                self.stream = stream

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def read(self, size):
                block = self.stream.read(size)
                if block and not changed[0]:
                    changed[0] = True
                    target.write_bytes(b'changed-during-read\n')
                return block

        @contextlib.contextmanager
        def file_open(fd, mode):
            with native_open(fd, mode) as stream:
                actual = os.fstat(stream.fileno())
                if mode == 'rb' and (actual.st_dev, actual.st_ino) == (target_stat.st_dev, target_stat.st_ino):
                    yield ObservedStream(stream)
                else:
                    yield stream

        with patch.object(api.os, 'fdopen', side_effect=file_open):
            with self.assertRaises(api.CandidateError):
                self.capture(base)
        self.assertTrue(changed[0])

    def test_head_drift_during_batch_is_rejected(self):
        base = self.changed_fixture()
        native = api._git

        def reader(root, args, limit, **kwargs):
            raw = native(root, args, limit, **kwargs)
            if args == ['cat-file', '--batch-check']:
                self.git('commit', '--allow-empty', '-qm', 'fixture move')
            return raw

        with patch.object(api, '_git', side_effect=reader):
            with self.assertRaises(api.CandidateError):
                self.capture(base)

    def test_last_query_cannot_publish_after_overall_deadline(self):
        (self.repo / 'a').write_bytes(b'unchanged\n')
        base = self.commit_fixture()
        native = api._git
        clock = [0.0]

        def reader(root, args, limit, **kwargs):
            raw = native(root, args, limit, **kwargs)
            if args == ['rev-parse', 'HEAD^{tree}']:
                clock[0] = 2.0
            return raw

        # Only the candidate clock is controlled; the real Git reader keeps
        # its native monotonic clock and actual process execution.
        with patch.object(api, 'time', SimpleNamespace(monotonic=lambda: clock[0])):
            with patch.object(api, '_git', side_effect=reader):
                with self.assertRaises(api.CandidateError):
                    self.capture(base, limits={'timeout_seconds': 1})

    def test_native_sha256_lfs_keeps_full_oid(self):
        self.repo = self.repo / 'sha256'
        self.repo.mkdir()
        self.git('init', '-q', '--object-format=sha256')
        self.git('config', 'core.autocrlf', 'false')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Native Fixture')
        data = b'payload'
        pointer = (b'version https://git-lfs.github.com/spec/v1\noid sha256:'
            + hashlib.sha256(data).hexdigest().encode() + b'\nsize 7\n')
        (self.repo / 'a').write_bytes(pointer)
        base = self.commit_fixture()
        expected = self.git('rev-parse', 'HEAD:a').decode().strip()
        (self.repo / 'a').write_bytes(data)
        result = self.capture(base)
        self.assertEqual(result['identity']['object_format'], 'sha256')
        self.assertEqual(len(expected), 64)
        self.assertEqual(result['manifest'][0]['git_oid'], expected)
        self.assertTrue(api.snapshot_identity_ok(result))

    def test_symlink_target_and_order_remain_actual_bytes(self):
        (self.repo / 'a').write_bytes(b'first\n')
        (self.repo / 'b').write_bytes(b'second\n')
        try:
            os.symlink('a', self.repo / 'link')
        except OSError as error:
            self.skipTest('native symlink creation unavailable: ' + str(error))
        self.git('config', 'core.symlinks', 'true')
        base = self.commit_fixture()
        (self.repo / 'link').unlink()
        os.symlink('b', self.repo / 'link')
        (self.repo / 'a').write_bytes(b'first\r\n')
        result = self.capture(base)
        self.assertEqual([row['path'] for row in result['manifest']], ['a', 'b', 'link'])
        row = result['manifest'][-1]
        self.assertEqual(row['kind'], 'symlink')
        self.assertEqual(row['sha256'], hashlib.sha256(b'b').hexdigest())

    def test_gitlink_hydrated_lfs_and_dirty_rejection(self):
        parent = self.repo
        child = parent / 'child'
        child.mkdir()
        self.repo = child
        self.git('init', '-q')
        self.git('config', 'core.autocrlf', 'false')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Native Fixture')
        data = b'child-payload'
        pointer = (b'version https://git-lfs.github.com/spec/v1\noid sha256:'
            + hashlib.sha256(data).hexdigest().encode() + b'\nsize 13\n')
        (child / 'a').write_bytes(pointer)
        child_head = self.commit_fixture()
        (child / 'a').write_bytes(data)
        self.repo = parent
        base = self.commit_fixture()
        result = self.capture(base)
        self.assertEqual(result['manifest'][0]['kind'], 'gitlink')
        self.assertEqual(result['manifest'][0]['git_oid'], child_head)
        (child / 'a').write_bytes(b'dirty')
        with self.assertRaises(api.CandidateError):
            self.capture(base)

    def test_batch_error_reaches_public_gate_without_writing_ticket(self):
        (self.repo / 'a').write_bytes(b'plain\n')
        self.commit_fixture()
        spec = importlib.util.spec_from_file_location('batch_failure_control', ROOT / 'tools/icode_control.py')
        control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(control)
        ticket = self.repo / '.icode_output/.icode_output_1'
        create = control.build_parser().parse_args(['create', '--dir', str(ticket),
            '--ticket-id', 'native-failure-fixture', '--birth', 'plan', '--requirement', 'fixture',
            '--metadata-json', '{"code_files":["a"]}', '--request-id', 'fixture-create'])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(control.cmd_create(create), 0)
        before = tuple((ticket / name).read_bytes() for name in (control.METADATA_NAME, control.EVENTS_NAME))
        (self.repo / 'a').write_bytes(b'plain\r\n')
        candidate = control.candidate_module()
        native = candidate._git

        def reader(root, args, limit, **kwargs):
            raw = native(root, args, limit, **kwargs)
            return b'broken metadata\n' if args == ['cat-file', '--batch-check'] else raw

        command = control.build_parser().parse_args(['candidate', '--dir', str(ticket),
            '--phase', 'capture', '--request-id', 'must-not-publish'])
        with patch.object(candidate, '_git', side_effect=reader):
            with self.assertRaises(control.ControlError) as failure:
                with contextlib.redirect_stdout(io.StringIO()):
                    control.cmd_candidate(command)
            self.assertEqual(failure.exception.report()['gate_id'], 'candidate_capture')
            eligibility = control.verification_applicability(ticket, control.load_metadata(ticket))
            self.assertFalse(eligibility['ok'])
            self.assertEqual(eligibility['state'], 'blocked')
        after = tuple((ticket / name).read_bytes() for name in (control.METADATA_NAME, control.EVENTS_NAME))
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
