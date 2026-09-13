"""Runtime 输入模型与调用前能力门。"""

from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Tuple


ALLOWED_CAPABILITIES = frozenset({"text", "image", "tools", "reasoning"})


class CapabilityError(ValueError):
    """模型/Backend 能力与请求载荷不匹配。"""


@dataclass(frozen=True)
class AgentRequest:
    ticket_dir: Path
    task_scope: str
    expected_artifact: str
    evidence_boundary: str
    join_condition: str
    backend: str
    model: str
    capabilities: FrozenSet[str]
    prompt: str
    request_id: str
    instructions: str = ""
    images: Tuple[str, ...] = ()
    max_output_tokens: int = 2048


def validate_request(request: AgentRequest) -> None:
    """在写 spawn 和网络调用前验证完整输入，失败时保持工单零副作用。"""
    text_fields = {
        "task_scope": request.task_scope,
        "expected_artifact": request.expected_artifact,
        "evidence_boundary": request.evidence_boundary,
        "join_condition": request.join_condition,
        "backend": request.backend,
        "model": request.model,
        "prompt": request.prompt,
        "request_id": request.request_id,
    }
    empty = sorted(key for key, value in text_fields.items()
                   if not isinstance(value, str) or not value.strip())
    if empty:
        raise CapabilityError(f"Agent request 缺非空字段: {empty}")
    capabilities = set(request.capabilities)
    invalid = sorted(capabilities - ALLOWED_CAPABILITIES)
    if invalid:
        raise CapabilityError(f"未知模型 capability: {invalid}")
    if "text" not in capabilities:
        raise CapabilityError("Agent 模型 capabilities 必须包含 text")
    if request.images and "image" not in capabilities:
        raise CapabilityError(
            "请求包含 image 输入，但模型 capabilities 未声明 image；禁止调用文本模型")
    if request.max_output_tokens <= 0:
        raise CapabilityError("max_output_tokens 必须大于 0")
    if not isinstance(request.ticket_dir, Path):
        raise CapabilityError("ticket_dir 必须是 pathlib.Path")
