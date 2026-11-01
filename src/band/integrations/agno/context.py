"""Task-local tools and execution observation for an admitted Agno run."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from band.core.protocols import AgentToolsProtocol

if TYPE_CHECKING:
    from band.integrations.agno.activity import RunActivity

current_tools: ContextVar[AgentToolsProtocol | None] = ContextVar(
    "agno_current_tools", default=None
)
current_activity: ContextVar[RunActivity | None] = ContextVar(
    "agno_current_activity", default=None
)
