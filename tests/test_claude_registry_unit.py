"""BOM input is valid; malformed input must retain corruption protection."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest


def registry():
    path = Path(__file__).resolve().parents[1] / "mcp/_lib/claude_registry.py"
    spec = importlib.util.spec_from_file_location("registry_bom_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_register_and_unregister_preserve_other_servers_and_unknown_fields(tmp_path, encoding):
    module = registry()
    config = tmp_path / "host.json"
    original = {"extra": {"keep": True}, "mcpServers": {"other": {"command": "keep"}}}
    config.write_text(json.dumps(original), encoding=encoding)
    with patch.object(module, "CLAUDE_JSON", config), patch.object(module, "ENTRY_DIR", tmp_path / "entries"):
        module.register("demo", {"command": "python", "args": []})
        assert module.inspect("demo")["command"] == "python"
        assert not config.read_bytes().startswith(b"\xef\xbb\xbf")
        assert module.unregister("demo") and not module.unregister("demo")
    assert json.loads(config.read_text(encoding="utf-8")) == original
    assert not list(tmp_path.glob("*.bak.*"))


@pytest.mark.parametrize("raw", [b'{invalid', b'[]', b'\xff', b'\xef\xbb\xbf{invalid'])
def test_corrupt_input_is_backed_up_and_never_overwritten(tmp_path, raw):
    module = registry()
    config = tmp_path / "host.json"
    config.write_bytes(raw)
    with patch.object(module, "CLAUDE_JSON", config), patch.object(module, "ENTRY_DIR", tmp_path / "entries"):
        with pytest.raises(RuntimeError):
            module.register("demo", {"command": "python"})
    assert config.read_bytes() == raw
    assert [p.read_bytes() for p in tmp_path.glob("*.bak.*")] == [raw]
    assert not (tmp_path / "entries").exists()


def test_export_only_does_not_touch_unselected_claude_config(tmp_path, monkeypatch):
    module = registry()
    config = tmp_path / "host.json"
    original = b'{"mcpServers":{"demo":{"command":"claude-owned"}}}'
    config.write_bytes(original)
    monkeypatch.setenv("ICODE_MCP_EXPORT_ONLY", "1")
    entry = {"command": "other-host-runtime", "args": []}
    with patch.object(module, "CLAUDE_JSON", config), patch.object(module, "ENTRY_DIR", tmp_path / "entries"):
        module.register("demo", entry)
    assert config.read_bytes() == original
    assert json.loads((tmp_path / "entries/demo.json").read_text(encoding="utf-8")) == entry


def test_stale_fixed_temporary_files_are_never_overwritten(tmp_path):
    module = registry()
    config, entries = tmp_path / "host.json", tmp_path / "entries"
    entries.mkdir()
    config_temp, entry_temp = config.with_suffix(".json.tmp"), entries / "demo.json.tmp"
    for path in (config_temp, entry_temp):
        path.write_bytes(b"unrelated existing file")
    with patch.object(module, "CLAUDE_JSON", config), patch.object(module, "ENTRY_DIR", entries):
        module.register("demo", {"command": "python"})
    assert config_temp.read_bytes() == entry_temp.read_bytes() == b"unrelated existing file"
