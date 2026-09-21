"""Opt-in real-host tests: offline, credential-free, no model turns.

ICODE_RUN_HOST_LOADING=1 enables locally installed native Codex/Claude binaries.
ICODE_SKILLS_CLI=/trusted/node_modules/skills/bin/cli.mjs enables Skills 1.7.0.
Missing opt-in/dependencies are SKIP, never host certification. Linux only.
"""
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    'claude-code': '.claude/skills', 'codebuddy': '.codebuddy/skills',
    'codex': '.agents/skills', 'cursor': '.agents/skills',
    'gemini-cli': '.agents/skills', 'opencode': '.agents/skills',
    'github-copilot': '.agents/skills', 'cline': '.agents/skills',
    'windsurf': '.windsurf/skills', 'antigravity': '.agents/skills',
}


def file_hashes(directory):
    result = {}
    for path in directory.rglob('*'):
        if path.is_symlink():
            raise AssertionError(f'unexpected symlink: {path}')
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class JsonStream:
    """Bounded line framing; initialization can emit unrelated notifications."""
    def __init__(self, process):
        self.process = process
        self.buffer = b''
        self.poller = selectors.DefaultSelector()
        self.poller.register(process.stdout, selectors.EVENT_READ)

    def send(self, message):
        self.process.stdin.write((json.dumps(message) + '\n').encode())
        self.process.stdin.flush()

    def receive(self, matches):
        deadline = time.monotonic() + 30
        total = 0
        while time.monotonic() < deadline:
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                message = json.loads(line)
                if matches(message):
                    return message
            if not self.poller.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise AssertionError('host EOF before initialization/discovery response')
            total += len(chunk)
            if total > 4 * 1024 * 1024:
                raise AssertionError('host initialization response exceeded 4 MiB')
            self.buffer += chunk
        raise AssertionError('host initialization/discovery timed out (30s)')


class HostLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (os.environ.get('ICODE_RUN_HOST_LOADING') == '1' or os.environ.get('ICODE_SKILLS_CLI')):
            raise unittest.SkipTest('real-host tests require explicit opt-in')
        if sys.platform != 'linux' or not shutil.which('bwrap'):
            raise unittest.SkipTest('requires Linux and bubblewrap network/filesystem isolation')
        cls.temporary = tempfile.TemporaryDirectory(prefix='icode-host-loading-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
        builder_home = cls.base / 'builder-home'
        builder_home.mkdir()
        builder_environment = {
            'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(builder_home),
            'TMPDIR': str(cls.base), 'PYTHONNOUSERSITE': '1',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
        }
        result = subprocess.run([
            sys.executable, '-B', str(ROOT / 'tools/build_skill_distribution.py'),
            '--source', str(ROOT), '--output', str(cls.base / 'icode'),
        ], capture_output=True, text=True, timeout=30, env=builder_environment)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.skills = cls.base / 'icode/skills'
        cls.names = {p.name for p in cls.skills.iterdir()}

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='case-', dir=self.base))
        for name in ('home', 'project', 'config', 'cache', 'data', 'codex-home'):
            (self.work / name).mkdir()
        # Deliberately do not inherit credentials, proxy state, MCP or host settings.
        self.environment = {
            'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp/work/home',
            'CODEX_HOME': '/tmp/work/codex-home', 'CLAUDE_CONFIG_DIR': '/tmp/work/claude-home',
            'XDG_CONFIG_HOME': '/tmp/work/config', 'XDG_CACHE_HOME': '/tmp/work/cache',
            'XDG_DATA_HOME': '/tmp/work/data', 'DO_NOT_TRACK': '1', 'DISABLE_TELEMETRY': '1',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'CI': '1',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
        }
        subprocess.run(self.sandbox([], ['/usr/bin/git', 'init', '--template=', '-q', '.']),
                       env=self.environment, check=True, capture_output=True, timeout=10)

    def sandbox(self, mounts, command):
        # A read-only / still exposes UNIX sockets; a network namespace alone
        # does not block filesystem AF_UNIX connections. Mount only runtimes.
        args = ['bwrap']
        for path in ('/usr', '/bin', '/sbin', '/lib', '/lib64'):
            if Path(path).exists():
                args.extend(['--ro-bind', path, path])
        for path in ('/etc/ld.so.cache', '/etc/os-release', '/etc/passwd', '/etc/group'):
            if Path(path).exists():
                args.extend(['--ro-bind', path, path])
        args += ['--dir', '/home', '--dir', '/root', '--dir', '/run',
                '--tmpfs', '/tmp', '--bind', str(self.work), '/tmp/work',
                '--unshare-net', '--unshare-pid', '--proc', '/proc', '--dev', '/dev',
                '--die-with-parent', '--chdir', '/tmp/work/project']
        for source, destination in mounts:
            args.extend(['--ro-bind', str(source), destination])
        return args + ['--remount-ro', '/', *command]

    def binary(self, name):
        if os.environ.get('ICODE_RUN_HOST_LOADING') != '1':
            self.skipTest('host loading not opted in')
        executable = shutil.which(name)
        if not executable:
            self.skipTest(f'{name} is not installed')
        path = Path(executable).resolve()
        with path.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                self.skipTest(f'{name} requires a native Linux binary for this isolated probe')
        return path

    def start(self, command):
        error = tempfile.TemporaryFile()
        self.addCleanup(error.close)
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=error, env=self.environment, bufsize=0)
        def stop():
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
        self.addCleanup(stop)
        stream = JsonStream(process)
        self.addCleanup(stream.poller.close)
        return stream

    def test_codex_discovers_shared_skills_and_reloads_removal(self):
        binary = self.binary('codex')
        target = self.work / 'project/.agents/skills'
        shutil.copytree(self.skills, target)
        stream = self.start(self.sandbox([(binary, '/tmp/codex')],
                            ['/tmp/codex', 'app-server', '--stdio']))
        stream.send({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'icode-loading-test', 'version': '1.0'}}})
        self.assertNotIn('error', stream.receive(lambda row: row.get('id') == 1))
        stream.send({'method': 'initialized'})
        skill = target / 'icode/SKILL.md'
        hidden = target / 'icode/SKILL.hidden'
        for identifier in (2, 3, 4, 5):
            if identifier == 4:
                skill.rename(hidden)
            elif identifier == 5:
                hidden.rename(skill)
            stream.send({'id': identifier, 'method': 'skills/list', 'params': {
                'cwds': ['/tmp/work/project'], 'forceReload': True}})
            response = stream.receive(lambda row: row.get('id') == identifier)
            self.assertNotIn('error', response)
            entries = response['result']['data']
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['errors'], [])
            found = [row for row in entries[0]['skills'] if row['scope'] == 'repo']
            expected = self.names - {'icode'} if identifier == 4 else self.names
            self.assertEqual({row['name'] for row in found}, expected)
            self.assertEqual(len(found), len(expected))
            self.assertTrue(all(row['enabled'] for row in found))
            for row in found:
                self.assertEqual(row['path'], f"/tmp/work/project/.agents/skills/{row['name']}/SKILL.md")

    def test_sandbox_does_not_expose_host_runtime_or_mounts(self):
        result = subprocess.run(self.sandbox([], ['/bin/sh', '-c',
            'test ! -e /run/user && test ! -e /mnt && test ! -e /media && test ! -e /var/run']),
            env=self.environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, 'host service/socket paths must not be mounted')

    def test_git_initialization_ignores_callers_redirect(self):
        outside = self.base / 'redirected-git'
        with patch.dict(os.environ, {'GIT_DIR': str(outside)}):
            self.setUp()
        self.assertFalse(outside.exists(), 'inherited GIT_DIR redirected initialization')
        self.assertTrue((self.work / 'project/.git').is_dir())

    def claude_commands(self, extra=()):
        binary = self.binary('claude')
        command = ['/tmp/claude', '-p', '--input-format', 'stream-json',
                   '--output-format', 'stream-json', '--verbose', '--strict-mcp-config',
                   '--tools', '', '--no-chrome', '--no-session-persistence',
                   '--permission-mode', 'dontAsk', '--setting-sources', 'project', *extra]
        stream = self.start(self.sandbox([(binary, '/tmp/claude'),
                            (self.base / 'icode', '/tmp/icode')], command))
        stream.send({'type': 'control_request', 'request_id': 'loading',
                     'request': {'subtype': 'initialize'}})
        reply = stream.receive(lambda row: row.get('type') == 'control_response'
                               and row['response'].get('request_id') == 'loading')['response']
        self.assertEqual(reply['subtype'], 'success', reply)
        return [row['name'] for row in reply['response']['commands']]

    def test_claude_project_discovery_and_repeat(self):
        self.binary('claude')
        shutil.copytree(self.skills, self.work / 'project/.claude/skills')
        for _ in range(2):
            commands = self.claude_commands()
            for name in self.names:
                self.assertEqual(commands.count(name), 1, name)

    def test_claude_no_skills_negative_control(self):
        self.assertFalse(self.names & set(self.claude_commands()))

    def test_claude_plugin_namespaced_discovery(self):
        commands = self.claude_commands(('--plugin-dir', '/tmp/icode'))
        for name in self.names:
            self.assertEqual(commands.count(f'icode:{name}'), 1, name)
        self.assertNotIn('icode', commands)

    def test_official_skills_cli_ten_target_copy_install_and_repeat(self):
        entry = os.environ.get('ICODE_SKILLS_CLI')
        if not entry:
            self.skipTest('set ICODE_SKILLS_CLI to a trusted Skills 1.7.0 cli.mjs')
        entry = Path(entry).resolve(strict=True)
        package = entry.parent.parent
        self.assertEqual(json.loads((package / 'package.json').read_text())['version'], '1.7.0')
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node.js is required')
        mounts = [(package.parent, '/tmp/node_modules'), (Path(node).resolve(), '/tmp/node'),
                  (self.base / 'icode', '/tmp/icode')]
        command = self.sandbox(mounts, ['/tmp/node', '/tmp/node_modules/skills/bin/cli.mjs'])
        expected = file_hashes(self.skills)
        for agent, relative in TARGETS.items():
            with self.subTest(agent=agent):
                # Each target has a separate project; no detected-agent spillover.
                project = self.work / 'project'
                previous = self.work / f'previous-{agent}'
                project.rename(previous)
                project.mkdir()
                for _ in range(2):
                    result = subprocess.run(command + ['add', '/tmp/icode', '--skill', '*',
                        '--agent', agent, '--copy', '--yes'], env=self.environment,
                        capture_output=True, text=True, timeout=45)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(file_hashes(project / relative), expected)
                result = subprocess.run(command + ['list', '--agent', agent],
                    env=self.environment, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(all(name in result.stdout for name in self.names))


if __name__ == '__main__':
    unittest.main()
