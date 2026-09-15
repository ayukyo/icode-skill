import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "verify_request", Path(__file__).resolve().parents[1] / "tools/verify_request.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
parse_request = module.parse_request


def test_build_retains_intent_without_executing_shell_text():
    request = '/icode verify --build --ticket T-1 用 "scripts/my build.sh" -j6 $(touch /tmp/no-run)'
    result = parse_request(request)
    assert result["action"] == "build"
    assert result["ticket"] == "T-1"
    assert result["raw_request"] == request
    assert "-j6 $(touch /tmp/no-run)" in result["natural_language"]


@pytest.mark.parametrize("text", [
    "--build --deploy", "--plan --build", "--listen --test target",
    "--build --reuse old", "--plan --reuse old", "--build --ticket",
    '--build --ticket " "', "--ticket A --ticket B --build", "--test",
    "/icode patch --build", '--build "unfinished',
    "--buid", "--build --builder", "--build --module module_a",
])
def test_invalid_requests_fail_before_any_action(text):
    with pytest.raises(ValueError):
        parse_request(text)


@pytest.mark.parametrize("text,action,reuse,target", [
    ("", "default", None, None),
    ("--deploy --reuse old", "deploy", "old", None),
    ("--reuse old --listen", "listen", "old", None),
    ("--test unit_a --reuse old", "device_test", "old", "unit_a"),
    ("--plan --ticket T-1", "plan", None, None),
])
def test_existing_modes_remain_available(text, action, reuse, target):
    result = parse_request(text)
    assert (result["action"], result["reuse"], result["target"]) == (action, reuse, target)


def test_explicit_separator_keeps_command_options_in_intent():
    result = parse_request("--build -- --module module_a --deploy 是脚本参数")
    assert result["action"] == "build"
    assert result["natural_language"] == "--module module_a --deploy 是脚本参数"
