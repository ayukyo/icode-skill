"""ICODE Agent Runtime 可替换模型后端。"""

from .base import AgentBackend, BackendError, BackendResult
from .fake import FakeBackend
from .openai_responses import OpenAIResponsesBackend

__all__ = [
    "AgentBackend",
    "BackendError",
    "BackendResult",
    "FakeBackend",
    "OpenAIResponsesBackend",
]
