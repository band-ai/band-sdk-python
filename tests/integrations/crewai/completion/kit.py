"""Kit: the custom action a scripted CrewAI model can call."""

from __future__ import annotations

from pydantic import BaseModel

from band.runtime.custom_tools import get_custom_tool_name
from tests.framework_conformance.turnprobes import ToolCall


class WorkInput(BaseModel):
    """Perform an action."""

    fail: bool = False


def work(*, fail: bool) -> ToolCall:
    """A call to the custom action, failing when ``fail``."""
    return ToolCall(get_custom_tool_name(WorkInput), {"fail": fail})
