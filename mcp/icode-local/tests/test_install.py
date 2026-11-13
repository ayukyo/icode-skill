from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from test_runtime import ROOT

@unittest.skipUnless(os.name == 'posix', 'Bash installer integration runs on Linux/WSL')
class InstallerTests(unittest.TestCase):
    def test_cli_profile_reads_only_selected_fake_codex_and_writes_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            sentinel = home / '.claude.json'; sentinel.write_text('unselected host must not be parsed')
            config = home / 'workspace.json'; config.write_text(json.dumps({'allowed_roots': [str(home)]}))
            data = home / 'codex-fixture.json'
            data.write_text(json.dumps({'icode-workspace': {'transport': {
                'command': 'python', 'args': [str(home / 'old/server.py')],
                'env': {'ICODE_WORKSPACE_CONFIG': str(config)}}}}))
            executable = home / 'codex'
            executable.write_text(f'#!{sys.executable}\nimport json,os,sys\n'
                                  'entries=json.load(open(os.environ["CODEX_FIXTURE"]))\n'
                                  'node=entries.get(sys.argv[3])\n'
                                  'if node is None:\n print("No MCP server",file=sys.stderr);sys.exit(1)\n'
                                  'print(json.dumps(node))\n')
            executable.chmod(0o755)
            target = home / 'new/icode-local'
            env = {**os.environ, 'HOME': str(home), 'ICODE_CODEX_BIN': str(executable), 'CODEX_FIXTURE': str(data)}
            command = [sys.executable, str(ROOT / 'mcp/icode-local/profile.py')]
            preflight = subprocess.run([*command, 'preflight', '--target', str(target), '--hosts', 'codex'],
                                       env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(preflight.returncode, 0, preflight.stderr)
            self.assertFalse(target.exists())
            setup = subprocess.run([*command, 'setup-profile', '--target', str(target), '--hosts', 'codex'],
                                   env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(setup.returncode, 0, setup.stderr)
            self.assertEqual(json.loads((target / 'config.json').read_text()),
                             {'module_configs': {'workspace': str(config)}})
            self.assertEqual(sentinel.read_text(), 'unselected host must not be parsed')

    def test_preflight_failure_happens_before_payload_or_registration_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            registry = home / '.claude.json'
            registry.write_text(json.dumps({'mcpServers': {'icode-evidence': {'args': ['/old/server.py'], 'env': {'ICODE_EVIDENCE_CONFIG': str(home / 'missing.json')}}}}))
            before = registry.read_bytes()
            tools = home / 'bin'; tools.mkdir()
            wrapper = tools / 'python3'
            wrapper.write_text(
                f'#!{sys.executable}\n'
                'import os, pathlib, sys\n'
                'if sys.argv[1:3] == ["-m", "venv"]:\n'
                ' p=pathlib.Path(sys.argv[3])/"bin";p.mkdir(parents=True)\n'
                f' (p/"python").symlink_to({sys.executable!r})\n'
                ' q=p/"pip";q.write_text("#!/bin/sh\\nexit 0\\n");q.chmod(0o755)\n'
                f'else: os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n')
            wrapper.chmod(0o755)
            selected = subprocess.run([str(wrapper), '-c', 'import sys; print(sys.executable)'],
                                      capture_output=True, text=True, timeout=5)
            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertEqual(selected.stdout.strip(), sys.executable)
            target = home / 'target'
            result = subprocess.run(['bash', str(ROOT / 'mcp/icode-local/install.sh')], env={**os.environ, 'HOME': str(home), 'PATH': str(tools) + ':' + os.environ['PATH'], 'ICODE_MCP_TARGET_BASE': str(target), 'ICODE_MCP_SELECTED_HOSTS': 'claude'}, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(registry.read_bytes(), before)
            self.assertFalse(target.exists(), 'failed preflight must not create the runtime')
            self.assertIn('preflight failed', result.stderr)

    def test_direct_install_stages_legacy_implementations_and_preserves_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            registry = home / '.claude.json'
            registry.write_text('{"mcpServers": {}}')
            before = registry.read_bytes()
            tools = home / 'bin'; tools.mkdir()
            wrapper = tools / 'python3'
            wrapper.write_text(
                f'#!{sys.executable}\n'
                'import os, pathlib, sys\n'
                'if sys.argv[1:3] == ["-m", "venv"]:\n'
                ' p=pathlib.Path(sys.argv[3])/"bin";p.mkdir(parents=True)\n'
                f' (p/"python").symlink_to({sys.executable!r})\n'
                ' q=p/"pip";q.write_text("#!/bin/sh\\nexit 0\\n");q.chmod(0o755)\n'
                f'else: os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n')
            wrapper.chmod(0o755)
            selected = subprocess.run([str(wrapper), '-c', 'import sys; print(sys.executable)'],
                                      capture_output=True, text=True, timeout=5)
            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertEqual(selected.stdout.strip(), sys.executable)
            target = home / 'target'
            legacy = target / 'icode-workspace'; legacy.mkdir(parents=True)
            existing = legacy / 'config.json'; existing.write_text('{"allowed_roots": ["/restricted"]}')
            runtime = legacy / '.venv'; runtime.mkdir(); (runtime / 'marker').write_text('keep')
            result = subprocess.run(['bash', str(ROOT / 'mcp/icode-local/install.sh')], env={**os.environ, 'HOME': str(home), 'PATH': str(tools) + ':' + os.environ['PATH'], 'ICODE_MCP_TARGET_BASE': str(target), 'ICODE_MCP_SELECTED_HOSTS': 'claude'}, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            for directory in ['icode-workspace', 'icode-evidence', 'icode-local-index', 'icode-mcp-health', 'icode-mcp-policy']:
                self.assertTrue((target / directory / 'server.py').is_file(), directory)
            self.assertEqual(json.loads(existing.read_text()), {'allowed_roots': ['/restricted']})
            self.assertEqual((runtime / 'marker').read_text(), 'keep')
            self.assertEqual(json.loads((target / 'icode-local/config.json').read_text()), {'module_configs': {}})
            verified = subprocess.run([sys.executable, str(ROOT / 'mcp/icode-local/tests/verify_stdio.py'),
                                       '--server', str(target / 'icode-local')],
                                      capture_output=True, text=True, timeout=35)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            (target / 'icode-local/config.json').write_text(json.dumps({'module_configs': {'workspace': str(existing)}}))
            profile_before = (target / 'icode-local/config.json').read_bytes()
            rebuilt = subprocess.run(['bash', str(ROOT / 'mcp/icode-local/install.sh'), '--full'],
                                     env={**os.environ, 'HOME': str(home), 'PATH': str(tools) + ':' + os.environ['PATH'],
                                          'ICODE_MCP_TARGET_BASE': str(target), 'ICODE_MCP_SELECTED_HOSTS': 'claude'},
                                     capture_output=True, text=True, timeout=20)
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
            self.assertEqual((target / 'icode-local/config.json').read_bytes(), profile_before)
            self.assertEqual((runtime / 'marker').read_text(), 'keep')

if __name__ == '__main__': unittest.main()
