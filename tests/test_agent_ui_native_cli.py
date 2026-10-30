"""Native defaults preserve explicit legacy browser invocations."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_cli', ROOT / 'tools/icode_agent.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


@pytest.mark.parametrize('platform,arguments,expected', [
    ('win32', [], 'window'), ('linux', [], 'browser'), ('darwin', [], 'browser'),
    ('win32', ['--browser'], 'browser'), ('win32', ['--port', '0'], 'browser'),
    ('win32', ['--no-browser'], 'browser'), ('win32', ['--window'], 'window'),
])
def test_transport_selection(platform, arguments, expected):
    args = cli.build_parser().parse_args(['ui', *arguments])
    assert cli.ui_transport(args, platform=platform) == expected


@pytest.mark.parametrize('arguments,platform', [
    (['--window', '--port', '0'], 'win32'), (['--window', '--no-browser'], 'win32'),
    (['--window'], 'linux'), (['--native-test-script', 'probe.json', '--browser'], 'win32'),
])
def test_incompatible_transport_rejected(arguments, platform):
    args = cli.build_parser().parse_args(['ui', *arguments])
    with pytest.raises(ValueError):
        cli.ui_transport(args, platform=platform)


def test_test_channel_requires_explicit_environment(monkeypatch):
    args = cli.build_parser().parse_args(['ui', '--window', '--native-test-script', 'probe.json'])
    monkeypatch.delenv('ICODE_RUN_NATIVE_UI_TESTS', raising=False)
    with pytest.raises(ValueError, match='ICODE_RUN_NATIVE_UI_TESTS'):
        cli.ui_transport(args, platform='win32')


def test_native_profile_and_registration_are_stable_and_context_bound(tmp_path):
    parser = cli.build_parser()
    global_args = parser.parse_args(['ui'])
    fixed_args = parser.parse_args(['ui', '--dir', str(tmp_path / 'ticket')])
    global_paths = cli.native_ui_context(global_args, tmp_path / 'data', tmp_path / 'cache')
    assert global_paths == cli.native_ui_context(global_args, tmp_path / 'data', tmp_path / 'cache')
    assert global_paths != cli.native_ui_context(fixed_args, tmp_path / 'data', tmp_path / 'cache')
    assert global_paths != cli.native_ui_context(global_args, tmp_path / 'other-data', tmp_path / 'cache')
    assert global_paths[0].is_relative_to(tmp_path / 'cache')
