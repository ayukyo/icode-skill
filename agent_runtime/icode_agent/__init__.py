"""ICODE Agent Runtime v0.4：可选执行层和本地 UI，不替代工单控制面。"""

from .coordinator import AgentCoordinator
from .models import AgentRequest, CapabilityError

__all__ = ["AgentCoordinator", "AgentRequest", "CapabilityError"]
__version__ = "0.4.0"
