"""PPT rendering failures must not hide host diagnostics or claim success."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "ppt_render_errors", Path(__file__).parents[1] / "tools/ppt/scripts/render_slides.py"
)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


@pytest.mark.parametrize("error", [
    FileNotFoundError("soffice not found"),
    subprocess.CalledProcessError(1, ["soffice"], stderr=b"GLIBC_2.38 not found"),
    RuntimeError("soffice did not produce a PDF"),
])
def test_cli_reports_render_failure_without_traceback(monkeypatch, capsys, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(renderer, "render", fail)
    monkeypatch.setattr(sys, "argv", ["render_slides.py", "deck.pptx", "preview"])
    assert renderer.main() == 1
    out = capsys.readouterr()
    assert not out.out
    assert "渲染失败" in out.err and "Traceback" not in out.err
    if isinstance(error, subprocess.CalledProcessError):
        assert "GLIBC_2.38 not found" in out.err


def test_cli_rejects_empty_render_result(monkeypatch, capsys):
    monkeypatch.setattr(renderer, "render", lambda *args: [])
    monkeypatch.setattr(sys, "argv", ["render_slides.py", "deck.pptx", "preview"])
    assert renderer.main() == 1
    assert "渲染失败" in capsys.readouterr().err
