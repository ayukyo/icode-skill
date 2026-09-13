"""单工单、单模型回合 Coordinator。"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

from .backends.base import AgentBackend, BackendError, BackendResult
from .models import AgentRequest, validate_request


class ControlPlaneError(RuntimeError):
    """控制面拒绝或不可用。"""


class LifecycleAmbiguityError(RuntimeError):
    """同一收费调用 request 已出现，禁止猜测并重放。"""


class AgentCoordinator:
    def __init__(self, control_path: Path):
        self.control_path = Path(control_path).resolve()

    def _control(self, args: List[str]) -> Dict:
        proc = subprocess.run(
            [sys.executable, str(self.control_path), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        raw = proc.stdout.strip() or proc.stderr.strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ControlPlaneError(
                f"控制面未返回合法 JSON（exit={proc.returncode}）") from exc
        if proc.returncode != 0 or not payload.get("ok"):
            gate = payload.get("gate_id", "control_plane")
            raise ControlPlaneError(f"{gate}: {payload.get('error', '控制面拒绝')}")
        return payload

    def run(self, request: AgentRequest, backend: AgentBackend) -> BackendResult:
        validate_request(request)
        spawn_args = [
            "record-agent-spawn",
            "--dir", str(request.ticket_dir),
            "--task-scope", request.task_scope,
            "--expected-artifact", request.expected_artifact,
            "--evidence-boundary", request.evidence_boundary,
            "--join-condition", request.join_condition,
            "--backend", request.backend,
            "--model", request.model,
        ]
        for capability in sorted(request.capabilities):
            spawn_args.extend(["--capability", capability])
        spawn_args.extend(["--request-id", f"{request.request_id}:spawn"])
        spawn_payload = self._control(spawn_args)
        if spawn_payload.get("already_applied"):
            raise LifecycleAmbiguityError(
                f"request_id={request.request_id!r} 已存在 Agent spawn；"
                "无法证明上次模型调用未发生，拒绝重放")
        spawn_id = spawn_payload["spawn"]["spawn_id"]
        try:
            result = backend.run(request)
        except BackendError as exc:
            self._record_result(
                request, spawn_id, result="failed", adopted="no",
                adoption_reason=f"Backend failed: {exc.code}",
                evidence_ref=f"error:{exc.code}", summary=str(exc),
                error_class=exc.code)
            raise
        except Exception as exc:
            normalized = BackendError(
                f"Backend 未分类异常: {type(exc).__name__}", code="backend_exception")
            self._record_result(
                request, spawn_id, result="failed", adopted="no",
                adoption_reason="Backend raised an unclassified exception",
                evidence_ref="error:backend_exception", summary=str(normalized),
                error_class=normalized.code)
            raise normalized from exc
        evidence_ref = (f"response:{result.response_id}" if result.response_id
                        else f"backend:{request.backend}")
        self._record_result(
            request, spawn_id, result="joined", adopted="yes",
            adoption_reason="Runtime returned a non-empty bounded model result",
            evidence_ref=evidence_ref, summary=result.output_text)
        return result

    def _record_result(self, request: AgentRequest, spawn_id: str, *, result: str,
                       adopted: str, adoption_reason: str, evidence_ref: str,
                       summary: str, error_class: str | None = None) -> Dict:
        args = [
            "record-agent-result",
            "--dir", str(request.ticket_dir),
            "--spawn-id", spawn_id,
            "--result", result,
            "--adopted", adopted,
            "--adoption-reason", adoption_reason,
            "--evidence-ref", evidence_ref,
            "--summary", summary,
            "--request-id", f"{request.request_id}:result",
        ]
        if error_class:
            args.extend(["--error-class", error_class])
        return self._control(args)

    def status(self, ticket_dir: Path) -> Dict:
        ticket_dir = Path(ticket_dir).resolve()
        # UI/CLI 只读投影由控制面在同一次校验读取中生成，避免二次直读的竞态窗口。
        projection = self._control(
            ["trace", "--dir", str(ticket_dir), "--limit", "1"])
        ticket_id = projection.get("ticket_id")
        if not isinstance(ticket_id, str) or not ticket_id:
            raise ControlPlaneError("控制面投影缺合法 ticket_id")
        spawns = projection.get("agent_spawns") or []
        if not isinstance(spawns, list) or any(not isinstance(item, dict) for item in spawns):
            raise ControlPlaneError("控制面投影 agent_spawns 结构非法")
        return {
            "schema_version": 1,
            "ticket_id": ticket_id,
            "spawns": spawns,
            "open_spawns": [item for item in spawns if "result" not in item],
        }
