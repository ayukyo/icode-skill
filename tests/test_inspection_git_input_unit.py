"""Bounded Git stdin contract and resource cleanup, using disposable fixtures."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('inspection_git_input', ROOT / 'tools/inspection_worklist.py')
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


class GitInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='icode-git-input-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        subprocess.run(['git', '-C', str(self.repo), 'init', '-q'], stdin=subprocess.DEVNULL,
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        result = subprocess.run(['git', '-C', str(self.repo), 'hash-object', '-w', '--stdin'],
            input=b'fixture\n', stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, check=True)
        self.oid = result.stdout.strip()

    def test_native_batch_input_and_default_read(self):
        before = sorted(str(path.relative_to(self.repo)) for path in self.repo.rglob('*'))
        data, truncated = api._git(self.repo, ['cat-file', '--batch-check'], 1024,
                                   input_data=self.oid + b'\n')
        self.assertFalse(truncated)
        self.assertEqual(data, self.oid + b' blob 8\n')
        data, truncated = api._git(self.repo, ['cat-file', 'blob', self.oid.decode()], 1024)
        self.assertEqual((data, truncated), (b'fixture\n', False))
        self.assertEqual(before, sorted(str(path.relative_to(self.repo)) for path in self.repo.rglob('*')))

    def test_invalid_or_oversized_input_rejected_before_process(self):
        for data in ('text', bytearray(b'x'), b'x' * 65):
            with self.subTest(type=type(data).__name__):
                with patch.object(subprocess, 'Popen', side_effect=AssertionError('must not start')):
                    with self.assertRaises(api.InspectionError):
                        api._git(self.repo, ['cat-file', '--batch'], 64, input_data=data)

    def test_overflow_is_reported_and_child_reaped(self):
        processes = []
        original = subprocess.Popen

        def launch(command, **kwargs):
            process = original(command, **kwargs)
            processes.append(process)
            return process

        with patch.object(subprocess, 'Popen', side_effect=launch):
            data, truncated = api._git(self.repo, ['cat-file', '--batch-check'], 90,
                                       input_data=(self.oid + b'\n') * 2)
        # Two input OIDs fit, while the two response headers exceed the bound.
        self.assertTrue(truncated)
        self.assertEqual(len(data), 90)
        self.assertTrue(processes)
        self.assertTrue(all(process.poll() is not None for process in processes))
        self.assertTrue(all(process.stdout.closed for process in processes))

    def test_timeout_reaps_child_and_closes_input(self):
        processes = []
        inputs = []
        original = subprocess.Popen

        def launch(command, **kwargs):
            inputs.append(kwargs.get('stdin'))
            process = original([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            processes.append(process)
            return process

        with patch.object(subprocess, 'Popen', side_effect=launch):
            with self.assertRaises(api.InspectionError):
                api._git(self.repo, ['cat-file', '--batch-check'], 1024,
                         input_data=self.oid + b'\n')
        self.assertTrue(all(process.poll() is not None for process in processes))
        self.assertTrue(all(process.stdout.closed for process in processes))
        self.assertTrue(inputs and all(stream.closed for stream in inputs))

    def test_initialization_error_reaps_child_and_closes_input(self):
        processes = []
        inputs = []
        original = subprocess.Popen

        def launch(command, **kwargs):
            inputs.append(kwargs.get('stdin'))
            process = original([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            processes.append(process)
            return process

        class FailingSelector:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def register(self, *args):
                raise OSError('injected selector registration failure')

        failure = (patch.object(api.Thread, 'start', side_effect=RuntimeError('injected thread start failure'))
                   if os.name == 'nt' else patch.object(api.selectors, 'DefaultSelector', FailingSelector))
        with patch.object(subprocess, 'Popen', side_effect=launch), failure:
            with self.assertRaises(api.InspectionError):
                api._git(self.repo, ['cat-file', '--batch-check'], 1024,
                         input_data=self.oid + b'\n')
        self.assertTrue(processes and all(process.poll() is not None for process in processes))
        self.assertTrue(all(process.stdout.closed for process in processes))
        self.assertTrue(inputs and all(stream.closed for stream in inputs))

    def test_input_write_failure_closes_file_without_starting_child(self):
        class FailedInput:
            closed = False

            def write(self, data):
                raise OSError('injected input write failure')

            def close(self):
                self.closed = True

        stream = FailedInput()
        with patch.object(api.tempfile, 'TemporaryFile', return_value=stream):
            with patch.object(subprocess, 'Popen', side_effect=AssertionError('must not start')):
                with self.assertRaises(api.InspectionError):
                    api._git(self.repo, ['cat-file', '--batch-check'], 1024,
                             input_data=self.oid + b'\n')
        self.assertTrue(stream.closed)

    def test_short_input_write_is_not_accepted(self):
        class ShortInput:
            closed = False

            def write(self, data):
                return len(data) - 1

            def close(self):
                self.closed = True

        stream = ShortInput()
        with patch.object(api.tempfile, 'TemporaryFile', return_value=stream):
            with patch.object(subprocess, 'Popen', side_effect=AssertionError('must not start')):
                with self.assertRaises(api.InspectionError):
                    api._git(self.repo, ['cat-file', '--batch-check'], 1024,
                             input_data=self.oid + b'\n')
        self.assertTrue(stream.closed)


if __name__ == '__main__':
    unittest.main()
