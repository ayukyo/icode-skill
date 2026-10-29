"""Managed PPT rendering reuses only the sibling DOCX owned toolchain."""
import builtins
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location(
    'ppt_managed_renderer', Path(__file__).parents[1] / 'tools/ppt/scripts/render_slides.py')
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


@pytest.fixture
def owned(tmp_path):
    bundle = tmp_path / 'owned'
    bundle.mkdir()
    binaries = {}
    for name in ('soffice', 'pdftoppm'):
        binary = bundle / name
        binary.write_bytes(b'owned test executable')
        binaries[name] = str(binary)
    return {'id': 'owned-fixture', 'bundle': str(bundle), **binaries}


def resolver_result(monkeypatch, result):
    monkeypatch.setitem(sys.modules, 'resolve_renderer', SimpleNamespace(resolve=lambda: result))


def test_legacy_cli_keeps_exact_three_argument_render_call(monkeypatch, capsys):
    def old_interface(pptx, out_dir, dpi):
        assert (pptx, out_dir, dpi) == (Path('deck.pptx'), Path('preview'), 144)
        return [out_dir / 'slide-1.png']
    monkeypatch.setattr(renderer, 'render', old_interface)
    original_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == 'resolve_renderer':
            raise AssertionError('legacy mode must not import the resolver')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(sys, 'argv', ['render_slides.py', 'deck.pptx', 'preview'])
    assert renderer.main() == 0
    assert 'Managed renderer:' not in capsys.readouterr().out


@pytest.mark.parametrize('dpi', ['0', '-2'])
@pytest.mark.parametrize('managed', [False, True])
def test_invalid_dpi_fails_before_resolving_or_rendering(monkeypatch, capsys, tmp_path, dpi, managed):
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid DPI must fail before doing work')
    monkeypatch.setattr(renderer, 'render', forbidden)
    monkeypatch.setitem(sys.modules, 'resolve_renderer', SimpleNamespace(resolve=forbidden))
    monkeypatch.setattr(sys, 'argv', ['render_slides.py', 'deck.pptx', str(tmp_path / 'preview'), '--dpi', dpi]
                        + (['--managed'] if managed else []))
    assert renderer.main() != 0
    assert 'DPI' in capsys.readouterr().err
    assert not (tmp_path / 'preview').exists()


def test_managed_cli_passes_owned_paths_and_reports_id(monkeypatch, capsys, owned):
    resolver_result(monkeypatch, {'status': 'ready', 'renderer': owned})
    calls = []
    def render_owned(pptx, out_dir, dpi, *, renderer):
        calls.append(renderer)
        return [out_dir / 'slide-1.png']
    monkeypatch.setattr(renderer, 'render', render_owned)
    monkeypatch.setattr(sys, 'argv', ['render_slides.py', 'deck.pptx', 'preview', '--managed'])
    assert renderer.main() == 0
    assert calls == [owned]
    assert 'owned-fixture' in capsys.readouterr().out


@pytest.mark.parametrize('status', ['unavailable', 'invalid_bundle', 'invalid_manifest'])
def test_managed_not_ready_fails_without_path_fallback(monkeypatch, capsys, status):
    resolver_result(monkeypatch, {'status': status, 'reason': 'owned toolchain unavailable'})
    def forbidden(*args, **kwargs):
        raise AssertionError('not-ready must not call a renderer')
    monkeypatch.setattr(renderer, 'render', forbidden)
    monkeypatch.setattr(sys, 'argv', ['render_slides.py', 'deck.pptx', 'preview', '--managed'])
    assert renderer.main() == 1
    stderr = capsys.readouterr().err
    assert status in stderr and 'bootstrap_renderer.py' in stderr
    assert 'Traceback' not in stderr


def test_managed_missing_component_fails_clearly(monkeypatch, capsys):
    original_import = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'resolve_renderer':
            raise ModuleNotFoundError('resolver component missing')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing)
    monkeypatch.setattr(sys, 'argv', ['render_slides.py', 'deck.pptx', 'preview', '--managed'])
    assert renderer.main() == 1
    stderr = capsys.readouterr().err
    assert 'resolve_renderer.py' in stderr and 'bootstrap_renderer.py' in stderr
    assert 'Traceback' not in stderr


def test_managed_rejects_relative_path_in_ready_record(monkeypatch, capsys, owned):
    resolver_result(monkeypatch, {'status': 'ready', 'renderer': {**owned, 'soffice': 'soffice'}})
    monkeypatch.setattr(sys, 'argv', ['render_slides.py', 'deck.pptx', 'preview', '--managed'])
    assert renderer.main() == 1
    assert 'owned' in capsys.readouterr().err


def test_render_uses_owned_binaries_and_only_current_pngs(monkeypatch, owned, tmp_path):
    out_dir = tmp_path / 'preview'
    out_dir.mkdir()
    (out_dir / 'slide-99.png').write_bytes(b'stale')
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == owned['soffice']:
            Path(command[command.index('--outdir') + 1], 'deck.pdf').write_bytes(b'%PDF')
        elif command[0] == owned['pdftoppm']:
            prefix = Path(command[-1])
            prefix.with_name(prefix.name + '-01.png').write_bytes(b'current PNG fixture')
        else:
            raise AssertionError('PATH fallback attempted')
    monkeypatch.setattr(renderer.subprocess, 'run', fake_run)
    pages = renderer.render(tmp_path / 'deck.pptx', out_dir, 144, renderer=owned)
    assert pages == [out_dir / 'slide-01.png']
    assert pages[0].read_bytes() == b'current PNG fixture'
    assert [call[0] for call in calls] == [owned['soffice'], owned['pdftoppm']]


def test_managed_relative_input_is_absolute_for_the_owned_wrapper(monkeypatch, owned, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == owned['soffice']:
            Path(command[command.index('--outdir') + 1], 'deck.pdf').write_bytes(b'%PDF')
        else:
            Path(command[-1] + '-1.png').write_bytes(b'current PNG fixture')
    monkeypatch.setattr(renderer.subprocess, 'run', fake_run)
    renderer.render(Path('deck.pptx'), tmp_path / 'preview', renderer=owned)
    assert calls[0][-1] == str(tmp_path / 'deck.pptx')


def test_managed_stale_output_does_not_hide_missing_pngs(monkeypatch, owned, tmp_path):
    out_dir = tmp_path / 'preview'
    out_dir.mkdir()
    (out_dir / 'slide-1.png').write_bytes(b'stale')
    def fake_run(command, **kwargs):
        if command[0] == owned['soffice']:
            Path(command[command.index('--outdir') + 1], 'deck.pdf').write_bytes(b'%PDF')
    monkeypatch.setattr(renderer.subprocess, 'run', fake_run)
    with pytest.raises(RuntimeError, match='did not produce any slide PNGs'):
        renderer.render(tmp_path / 'deck.pptx', out_dir, renderer=owned)
    assert (out_dir / 'slide-1.png').read_bytes() == b'stale'


def test_copied_ppt_tool_without_docx_component_has_clear_failure(tmp_path):
    copied = tmp_path / 'tools/ppt/scripts/render_slides.py'
    copied.parent.mkdir(parents=True)
    copied.write_bytes(Path(SPEC.origin).read_bytes())
    result = subprocess.run([sys.executable, '-B', str(copied), 'deck.pptx', str(tmp_path / 'preview'), '--managed'],
                            text=True, encoding='utf-8', capture_output=True)
    assert result.returncode == 1
    assert 'resolve_renderer.py' in result.stderr and 'bootstrap_renderer.py' in result.stderr
    assert 'Traceback' not in result.stderr
    assert not (tmp_path / 'preview').exists()
