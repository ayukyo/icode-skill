"""Whole-request admission reuses action-policy; it never executes the task."""
import contextlib
import io
import json
import sys
from unittest.mock import patch

import pytest

import test_candidate_action_policy_provider_unit as policies


@pytest.fixture
def fixture():
    f = policies.ActionPolicyProviderTests(methodName="runTest")
    f.setUp()
    yield f
    f.doCleanups()


def call(f, *args, provider=None, factory=None):
    output = io.StringIO()
    with patch.object(sys, "argv", ["icode_control.py", "action-policy", "--dir",
                                   str(f.f.ticket), *args]), contextlib.redirect_stdout(output):
        code = policies.ctl.main(git_provider=provider,
                                 require_git_provider=provider is not None,
                                 inspection_provider_factory=factory)
    return code, json.loads(output.getvalue())


def test_local_whole_request_checks_all_required_steps_without_writing(fixture):
    f = fixture
    before = f.frozen()
    code, result = call(f, "--required-step", "code", "--required-step", "audit")
    assert code == 0, result
    preflight = result["capability_preflight"]
    assert preflight["task_supported"] is True
    assert preflight["required_steps"] == ["code", "audit"]
    assert preflight["unsupported_steps"] == []
    assert preflight["runtime_verified"] is False
    assert f.frozen() == before


def test_protected_default_projection_does_not_recommend_unsupported_plan(fixture):
    f = fixture
    provider = f.seal()
    before = f.frozen()
    code, result = call(f, provider=provider)
    assert code == 0, result
    assert result["allowed_actions"] == ["status"]
    assert result["recommended_action"] is None
    assert result["blocked_reason"] == "workflow_capability"
    preflight = result["capability_preflight"]
    assert preflight["profile"] == "protected_provider"
    assert preflight["steps"]["audit"]["supported"] is False
    assert preflight["steps"]["code"]["reason"] == "inspection_provider_required"
    assert f.frozen() == before


def test_protected_full_request_fails_before_any_work_and_explains_recovery(fixture):
    f = fixture
    provider = f.seal()
    before = f.frozen()
    factory = lambda *args: pytest.fail("preflight must not start inspection transport")
    code, result = call(f, "--required-step", "code", "--required-step", "deepcheck",
                        "--required-step", "audit", provider=provider, factory=factory)
    assert code == 1, result
    assert result["gate_id"] == "workflow_capability"
    preflight = result["capability_preflight"]
    assert preflight["unsupported_steps"] == ["deepcheck", "audit"]
    assert preflight["steps"]["code"]["supported"] is True
    assert preflight["task_supported"] is False
    assert preflight["recovery"]["automatic_fallback"] is False
    assert preflight["recovery"]["requires_user_confirmation"] is False
    assert f.frozen() == before


def test_code_only_provider_admission_is_not_runtime_acceptance(fixture):
    f = fixture
    provider = f.seal()
    factory = lambda *args: pytest.fail("no provider activation in capability projection")
    before = f.frozen()
    code, result = call(f, "--required-step", "code", provider=provider, factory=factory)
    assert code == 0, result
    preflight = result["capability_preflight"]
    assert preflight["task_supported"] is True
    assert preflight["runtime_verified"] is False
    assert preflight["boundary"] == "control_plane_dispatch"
    assert f.frozen() == before


def test_unknown_required_step_cannot_silently_pass(fixture):
    f = fixture
    before = f.frozen()
    code, result = call(f, "--required-step", "made_up_step")
    assert code == 2, result
    assert result["gate_id"] == "workflow_capability_contract"
    assert f.frozen() == before


def test_protected_explicit_action_rejects_missing_backend_before_input_capture(fixture):
    f = fixture
    provider = f.seal()
    before = f.frozen()
    with patch.object(policies.ctl, "capture_step_inputs",
                      side_effect=AssertionError("unsupported work must not start")):
        code, result = call(f, "--action", "plan", provider=provider)
    assert code == 1, result
    assert result["gate_id"] == "workflow_capability"
    assert result["capability_preflight"]["unsupported_steps"] == ["plan"]
    assert f.frozen() == before


def test_factory_without_candidate_context_cannot_claim_protected_support(fixture):
    f = fixture
    before = f.frozen()
    code, result = call(f, "--required-step", "code", factory=lambda *args: None)
    assert code == 1, result
    assert result["gate_id"] == "candidate_provider_context"
    assert f.frozen() == before
