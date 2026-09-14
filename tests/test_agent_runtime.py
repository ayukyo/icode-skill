import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent_runtime"))

import icode_agent

from icode_agent.backends.base import BackendError, BackendResult
from icode_agent.backends.fake import FakeBackend
from icode_agent.backends.openai_responses import OpenAIResponsesBackend
from icode_agent.coordinator import (
    AgentCoordinator,
    ControlPlaneError,
    LifecycleAmbiguityError,
)
from icode_agent.models import AgentRequest, CapabilityError


CONTROL = ROOT / "tools" / "icode_control.py"


def test_runtime_version_matches_v04_ui_contract():
    assert icode_agent.__version__ == "0.4.0"


def make_ticket(tmp_path: Path, name: str = "agent-runtime-1") -> Path:
    out_dir = tmp_path / ".icode_output" / ".icode_output_1"
    subprocess.run(
        [
            sys.executable,
            str(CONTROL),
            "create",
            "--dir",
            str(out_dir),
            "--ticket-id",
            name,
            "--requirement",
            "agent runtime test",
            "--birth",
            "init",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return out_dir


def request(out_dir: Path, **overrides) -> AgentRequest:
    values = {
        "ticket_dir": out_dir,
        "task_scope": "review bounded facts",
        "expected_artifact": "structured answer",
        "evidence_boundary": "ticket-only",
        "join_condition": "non-empty model output",
        "backend": "fake",
        "model": "fake-v1",
        "capabilities": frozenset({"text"}),
        "prompt": "Summarize the evidence",
        "request_id": "turn-1",
        "instructions": "Return only bounded facts.",
        "images": (),
        "max_output_tokens": 512,
    }
    values.update(overrides)
    return AgentRequest(**values)


def load_metadata(out_dir: Path) -> dict:
    return json.loads((out_dir / ".ico_metadata.json").read_text(encoding="utf-8"))


def test_fake_backend_success_records_terminal_lifecycle(tmp_path):
    out_dir = make_ticket(tmp_path)
    backend = FakeBackend(response="bounded result")

    result = AgentCoordinator(control_path=CONTROL).run(request(out_dir), backend)

    assert result.output_text == "bounded result"
    assert backend.calls == 1
    spawn = load_metadata(out_dir)["extensions"]["agent"]["spawns"][0]
    assert spawn["result"] == "joined"
    assert spawn["adopted"] == "yes"
    assert spawn["summary_digest"] == hashlib.sha256(b"bounded result").hexdigest()
    assert "bounded result" not in (out_dir / ".ico_events.jsonl").read_text(
        encoding="utf-8"
    )


def test_text_only_request_rejects_image_before_spawn(tmp_path):
    out_dir = make_ticket(tmp_path)
    backend = FakeBackend(response="must not run")
    req = request(out_dir, images=("data:image/png;base64,AAAA",))

    with pytest.raises(CapabilityError, match="image"):
        AgentCoordinator(control_path=CONTROL).run(req, backend)

    assert backend.calls == 0
    assert "agent" not in load_metadata(out_dir).get("extensions", {})


def test_backend_failure_is_recorded_as_failed_and_not_adopted(tmp_path):
    out_dir = make_ticket(tmp_path)
    backend = FakeBackend(error=BackendError("provider timeout", code="provider_timeout"))

    with pytest.raises(BackendError, match="provider timeout"):
        AgentCoordinator(control_path=CONTROL).run(request(out_dir), backend)

    spawn = load_metadata(out_dir)["extensions"]["agent"]["spawns"][0]
    assert spawn["result"] == "failed"
    assert spawn["adopted"] == "no"
    assert spawn["error_class"] == "provider_timeout"
    assert "provider timeout" not in (out_dir / ".ico_events.jsonl").read_text(
        encoding="utf-8"
    )


def test_same_request_never_replays_a_model_call(tmp_path):
    out_dir = make_ticket(tmp_path)
    backend = FakeBackend(response="one paid response")
    coordinator = AgentCoordinator(control_path=CONTROL)
    req = request(out_dir)

    coordinator.run(req, backend)
    with pytest.raises(LifecycleAmbiguityError, match="request_id"):
        coordinator.run(req, backend)

    assert backend.calls == 1


class FakeResponses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return type(
            "Response",
            (),
            {
                "id": "resp-test-1",
                "output_text": "vision result",
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
        )()


class FakeOpenAIClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_openai_backend_builds_capability_checked_responses_request(tmp_path):
    client = FakeOpenAIClient()
    backend = OpenAIResponsesBackend(client=client)
    req = request(
        make_ticket(tmp_path),
        backend="openai-responses",
        model="configured-model",
        capabilities=frozenset({"text", "image"}),
        images=("https://example.invalid/slide.png",),
    )

    result = backend.run(req)

    assert result == BackendResult(
        output_text="vision result",
        response_id="resp-test-1",
        usage={"input_tokens": 10, "output_tokens": 3},
    )
    sent = client.responses.kwargs
    assert sent["model"] == "configured-model"
    assert sent["instructions"] == "Return only bounded facts."
    assert sent["max_output_tokens"] == 512
    content = sent["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": "Summarize the evidence"}
    assert content[1] == {
        "type": "input_image",
        "image_url": "https://example.invalid/slide.png",
    }
    assert sent["metadata"]["ticket_id"] == "agent-runtime-1"


def test_status_has_versioned_ui_read_model(tmp_path):
    out_dir = make_ticket(tmp_path)
    coordinator = AgentCoordinator(control_path=CONTROL)
    status = coordinator.status(out_dir)
    assert status == {
        "schema_version": 1,
        "ticket_id": "agent-runtime-1",
        "spawns": [],
        "open_spawns": [],
    }


def test_status_rejects_metadata_not_backed_by_event_chain(tmp_path):
    out_dir = make_ticket(tmp_path)
    metadata = load_metadata(out_dir)
    metadata["extensions"] = {
        "agent": {
            "spawns": [
                {
                    "spawn_id": "forged",
                    "at": "2026-09-13T00:00:00+00:00",
                    "task_scope": "forged",
                    "expected_artifact": "forged",
                    "evidence_boundary": "forged",
                    "join_condition": "forged",
                    "backend": "fake",
                    "model": "fake-v1",
                    "capabilities": ["text"],
                }
            ]
        }
    }
    (out_dir / ".ico_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ControlPlaneError, match="event_chain"):
        AgentCoordinator(control_path=CONTROL).status(out_dir)


def test_status_uses_control_plane_projection_without_second_file_read(
    tmp_path, monkeypatch
):
    out_dir = make_ticket(tmp_path)

    def reject_parent_side_read(*_args, **_kwargs):
        raise AssertionError("status 不应在控制面校验后再次直读 metadata")

    monkeypatch.setattr(Path, "read_text", reject_parent_side_read)

    status = AgentCoordinator(control_path=CONTROL).status(out_dir)

    assert status["ticket_id"] == "agent-runtime-1"
    assert status["spawns"] == []


def test_api_key_never_appears_in_runtime_result_or_ticket(tmp_path, monkeypatch):
    secret = "sk-test-never-persist"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    out_dir = make_ticket(tmp_path)

    result = AgentCoordinator(control_path=CONTROL).run(
        request(out_dir), FakeBackend(response="safe")
    )

    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    ticket_text = "".join(
        path.read_text(encoding="utf-8")
        for path in (out_dir / ".ico_metadata.json", out_dir / ".ico_events.jsonl")
    )
    assert secret not in serialized
    assert secret not in ticket_text
