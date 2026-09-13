"""确定性离线 Backend，仅供合同测试和 demo 模拟。"""

from .base import BackendError, BackendResult
from ..models import AgentRequest, validate_request


class FakeBackend:
    def __init__(self, response: str = "fake response", error: BackendError | None = None):
        self.response = response
        self.error = error
        self.calls = 0

    def run(self, request: AgentRequest) -> BackendResult:
        validate_request(request)
        self.calls += 1
        if self.error is not None:
            raise self.error
        if not self.response:
            raise BackendError("FakeBackend 返回空文本", code="empty_output")
        return BackendResult(
            output_text=self.response,
            response_id=f"fake:{request.request_id}",
            usage={"input_tokens": 0, "output_tokens": 0},
        )
