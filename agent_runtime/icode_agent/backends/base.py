"""Provider-neutral Backend 协议。"""

from dataclasses import dataclass
from typing import Any, Dict, Protocol

from ..models import AgentRequest


class BackendError(RuntimeError):
    """可安全记录错误类别、但不泄露凭据或完整 provider 响应。"""

    def __init__(self, message: str, code: str = "backend_error"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BackendResult:
    output_text: str
    response_id: str | None = None
    usage: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "output_text": self.output_text,
            "response_id": self.response_id,
            "usage": self.usage,
        }


class AgentBackend(Protocol):
    def run(self, request: AgentRequest) -> BackendResult:
        """执行一个有界模型回合；不得直接修改工单。"""
