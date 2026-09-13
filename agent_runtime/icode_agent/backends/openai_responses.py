"""OpenAI Responses API 适配；SDK 为显式调用时才需要的可选依赖。"""

from typing import Any, Dict

from .base import BackendError, BackendResult
from ..models import AgentRequest, validate_request


def _usage_to_dict(usage: Any) -> Dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return dict(usage)
    if hasattr(usage, "model_dump"):
        value = usage.model_dump()
        return value if isinstance(value, dict) else None
    result = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            result[key] = value
    return result or None


class OpenAIResponsesBackend:
    def __init__(self, client=None):
        self._client = client

    def _client_or_raise(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise BackendError(
                "OpenAI Backend 未安装可选依赖 openai",
                code="backend_unavailable",
            ) from exc
        self._client = OpenAI()
        return self._client

    def run(self, request: AgentRequest) -> BackendResult:
        validate_request(request)
        content = [{"type": "input_text", "text": request.prompt}]
        content.extend(
            {"type": "input_image", "image_url": image}
            for image in request.images
        )
        metadata = {
            "ticket_id": _ticket_id(request),
            "request_id": request.request_id,
            "backend": request.backend,
        }
        try:
            response = self._client_or_raise().responses.create(
                model=request.model,
                instructions=request.instructions,
                input=[{"role": "user", "content": content}],
                max_output_tokens=request.max_output_tokens,
                metadata=metadata,
            )
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(
                f"OpenAI Responses 调用失败: {type(exc).__name__}",
                code="provider_error",
            ) from exc
        output_text = getattr(response, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise BackendError("OpenAI Responses 返回空文本", code="empty_output")
        return BackendResult(
            output_text=output_text,
            response_id=getattr(response, "id", None),
            usage=_usage_to_dict(getattr(response, "usage", None)),
        )


def _ticket_id(request: AgentRequest) -> str:
    """只读取本地身份；不把 ticket 路径或正文发送给 provider。"""
    import json

    metadata_path = request.ticket_dir / ".ico_metadata.json"
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackendError("无法读取工单 ticket_id", code="ticket_identity") from exc
    ticket_id = data.get("ticket_id")
    if not isinstance(ticket_id, str) or not ticket_id:
        raise BackendError("工单缺合法 ticket_id", code="ticket_identity")
    return ticket_id
