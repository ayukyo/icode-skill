"""Installer shell contract; every subprocess uses a temporary home and fake tools."""
import os
from pathlib import Path
import shutil
import subprocess
import importlib.util
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVERS = ("context7", "memory", "playwright", "sequential-thinking")


def bash_binary():
    if os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parents[1] / "bin/bash.exe" if git else Path("missing")
        if candidate.is_file():
            return str(candidate)
        pytest.skip("Git Bash is required")
    return shutil.which("bash") or pytest.skip("bash is required")


@pytest.fixture
def sandbox(tmp_path):
    tools = tmp_path / "tools with spaces"
    tools.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    for server in SERVERS:
        target = tmp_path / "mcp" / server
        (target / "scripts").mkdir(parents=True)
        shutil.copyfile(ROOT / "mcp" / server / "install.sh", target / "install.sh")
        (target / "scripts/register_mcp.py").touch()
        (target / "uninstall.sh").write_text("#!/bin/bash\nprintf 'uninstall\\n' >> \"$TEST_LOG\"\n", encoding="utf-8")
    lib = tmp_path / "mcp/_lib"
    lib.mkdir()
    for name in ("install_managed_node_mcp.sh", "find_node.py"):
        source = ROOT / "mcp/_lib" / name
        if source.exists():
            shutil.copyfile(source, lib / name)

    def tool(name, content):
        path = tools / name
        path.write_text("#!/bin/bash\n" + content, encoding="utf-8")
        path.chmod(0o755)
        return path

    tool("dirname", 'printf "%s\\n" "${1%/*}"\n')
    # This fake exercises argument boundaries without allowing registration or npm.
    python = tool("real-python", '''
if [ "$1" = "-3" ]; then shift; fi
if [ "$1" = "-c" ]; then printf '1\\n'; exit 0; fi
case "$1" in
  */find_node.py) printf '%s\\t%s\\n' "$TEST_NODE" "${TEST_NPX:-}"; exit 0 ;;
  */register_mcp.py)
    shift
    printf 'register\\n' >> "$TEST_LOG"
    printf '<%s>\\n' "$@" >> "$TEST_LOG"
    exit "${TEST_REGISTER_EXIT:-0}" ;;
  */claude_registry.py)
    printf 'unregister:%s:%s\\n' "$2" "$3" >> "$TEST_LOG"; exit 0 ;;
esac
exit 9
''')
    tool("python3", "exit 0\n")  # WindowsApps-style empty success stub.
    tool("python", "exit 0\n")
    log = tmp_path / "actions.log"
    env = os.environ.copy()
    for name in ("ICODE_PYTHON_BIN", "ICODE_NODE_BIN", "ICODE_NPX_BIN"):
        env.pop(name, None)
    env.update(PATH=str(tools), HOME=str(home), USERPROFILE=str(home),
               TEST_LOG=str(log), TEST_NODE="/absolute/node with spaces/node", MSYS_NO_PATHCONV="1")

    def run(server="memory", args=(), **overrides):
        return subprocess.run([bash_binary(), str(tmp_path / "mcp" / server / "install.sh"), *args],
                              env={**env, **overrides}, capture_output=True, text=True, encoding="utf-8", timeout=15)
    return run, env, python, log, home, tool


@pytest.mark.parametrize("server", SERVERS)
def test_explicit_python_skips_stub_and_default_needs_no_npx(sandbox, server):
    run, env, python, log, home, tool = sandbox
    result = run(server, ICODE_PYTHON_BIN=str(python))
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text().splitlines() == ["register", "</absolute/node with spaces/node>", "<>"]
    assert list(home.iterdir()) == []


def test_empty_success_python_stubs_cannot_write(sandbox):
    run, env, python, log, home, tool = sandbox
    result = run()
    assert result.returncode != 0
    assert "Python" in result.stdout + result.stderr
    assert not log.exists()
    assert list(home.iterdir()) == []


def test_py_launcher_keeps_minus_three_argument(sandbox):
    run, env, python, log, home, tool = sandbox
    tool("py", 'if [ "$1" != "-3" ]; then exit 8; fi\nshift\n' + python.read_text().split("\n", 1)[1])
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text().splitlines()[-1] == "<>"


def test_custom_package_keeps_legacy_npx_and_argument(sandbox):
    run, env, python, log, home, tool = sandbox
    result = run(args=("@custom/server@2.0.0",), ICODE_PYTHON_BIN=str(python), TEST_NPX="/absolute/npm tools/npx")
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text().splitlines() == ["register", "</absolute/node with spaces/node>",
                                           "</absolute/npm tools/npx>", "<@custom/server@2.0.0>"]


@pytest.mark.parametrize("package", ["@modelcontextprotocol/server-memory", "@modelcontextprotocol/server-memory@1.0.0"])
def test_explicit_managed_package_does_not_need_npx(sandbox, package):
    run, env, python, log, home, tool = sandbox
    result = run(args=(package,), ICODE_PYTHON_BIN=str(python))
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text().splitlines() == ["register", "</absolute/node with spaces/node>", "<>", "<" + package + ">"]


def test_runtime_install_failure_propagates(sandbox):
    run, env, python, log, home, tool = sandbox
    result = run(ICODE_PYTHON_BIN=str(python), TEST_REGISTER_EXIT="7")
    assert result.returncode == 7
    assert "完成" not in result.stdout
    assert list(home.iterdir()) == []


def test_uninstall_does_not_require_node_or_npx(sandbox):
    run, env, python, log, home, tool = sandbox
    result = run(args=("--uninstall",), ICODE_PYTHON_BIN=str(python), TEST_NODE="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text().splitlines() == ["unregister:unregister:memory"]


def test_unknown_option_rejected_without_registration(sandbox):
    run, env, python, log, home, tool = sandbox
    result = run(args=("--unexpected",), ICODE_PYTHON_BIN=str(python))
    assert result.returncode != 0
    assert not log.exists()


def test_invalid_explicit_python_does_not_fall_back_or_write(sandbox):
    run, env, python, log, home, tool = sandbox
    tool("py", python.read_text().split("\n", 1)[1])
    invalid = tool("explicit-stub", "exit 0\n")
    result = run(ICODE_PYTHON_BIN=str(invalid))
    assert result.returncode != 0
    assert "ICODE_PYTHON_BIN" in result.stdout + result.stderr
    assert not log.exists()


def test_missing_node_cannot_register(sandbox):
    run, env, python, log, home, tool = sandbox
    result = run(ICODE_PYTHON_BIN=str(python), TEST_NODE="")
    assert result.returncode != 0
    assert not log.exists()


def test_extra_arguments_cannot_register(sandbox):
    run, env, python, log, home, tool = sandbox
    result = run(args=("@custom/server", "unexpected"), ICODE_PYTHON_BIN=str(python))
    assert result.returncode != 0
    assert not log.exists()


def test_find_node_node_only_accepts_missing_npx(monkeypatch, capsys):
    path = ROOT / "mcp/_lib/find_node.py"
    spec = importlib.util.spec_from_file_location("node_installer_find_node", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "find_node_npx", lambda: ("/absolute/node", None))
    monkeypatch.setattr(module.sys, "argv", [str(path), "--node-only"])
    assert module.main() == 0
    assert capsys.readouterr().out == "/absolute/node\t\n"


def load_stdio_verifier():
    path = ROOT / "tests/verify_node_direct_stdio.py"
    assert path.is_file(), "The thread-based stdio verification utility is missing"
    spec = importlib.util.spec_from_file_location("node_direct_stdio_verifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stdio_client_roundtrip_and_eof_close(tmp_path):
    module = load_stdio_verifier()
    server = tmp_path / "server with spaces.py"
    server.write_text('''import json,sys
for line in sys.stdin:
    message=json.loads(line)
    if "id" in message:
        print(json.dumps({"jsonrpc":"2.0","id":message["id"],"result":{"echo":message["params"]}}),flush=True)
''', encoding="utf-8")
    with module.StdioClient([sys.executable, str(server)], os.environ.copy(), timeout=3) as client:
        assert client.request("example", {"value": "fixed-test"}) == {"echo": {"value": "fixed-test"}}
    assert client.process.poll() == 0


def test_stdio_client_abnormal_exit_is_bounded(tmp_path):
    module = load_stdio_verifier()
    with pytest.raises(RuntimeError, match="EOF|exited|pipe"):
        with module.StdioClient([sys.executable, "-c", "raise SystemExit(7)"], os.environ.copy(), timeout=3) as client:
            client.request("initialize", {})
    assert client.process.poll() == 7


def test_stdio_client_timeout_closes_process(tmp_path):
    module = load_stdio_verifier()
    with pytest.raises(TimeoutError):
        with module.StdioClient([sys.executable, "-c", "import time; time.sleep(60)"], os.environ.copy(), timeout=0.15) as client:
            client.request("initialize", {})
    assert client.process.poll() is not None
