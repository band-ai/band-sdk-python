"""Room context and tool-event reporting for the CrewAI integration.

A reporter decides whether a tool call and its result reach the platform as
``tool_call`` / ``tool_result`` events. Each CrewAI adapter supplies its own,
so the tool wrappers stay unaware of how (or whether) a turn is narrated.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from band.core.protocols import AgentToolsProtocol
from band.core.types import AdapterFeatures, Emit, ToolEventKey

logger = logging.getLogger(__name__)


# --- Shared context + reporter contracts ---


@dataclass
class ReplyTracker:
    """Mutable per-turn markers shared (by reference) with the tool wrappers.

    ``replied`` flips once ``band_send_message`` succeeds; ``tool_executed`` flips
    once any terminal tool succeeds.
    """

    replied: bool = False
    tool_executed: bool = False


@dataclass(frozen=True)
class CrewAIToolContext:
    """The room a tool call belongs to, plus the tools it runs against."""

    room_id: str
    tools: AgentToolsProtocol
    reply_tracker: ReplyTracker | None = None


@runtime_checkable
class CrewAIToolReporter(Protocol):
    """Hook for tool execution event emission.

    Implementations decide whether to send tool_call / tool_result events to
    the platform. The default EmitToolCallsReporter gates emission on
    Emit.TOOL_CALLS. NoopReporter never emits.

    Both methods are best-effort: implementations must not raise on transport
    failure. Wrappers depend on this contract.
    """

    async def report_call(
        self,
        tools: AgentToolsProtocol,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> None: ...

    async def report_result(
        self,
        tools: AgentToolsProtocol,
        tool_name: str,
        result: Any,
        is_error: bool = False,
    ) -> None: ...


class EmitToolCallsReporter:
    """Reporter gated by Emit.TOOL_CALLS — matches legacy CrewAIAdapter behavior."""

    def __init__(self, features: AdapterFeatures) -> None:
        self._features = features

    async def report_call(
        self,
        tools: AgentToolsProtocol,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> None:
        if Emit.TOOL_CALLS not in self._features.emit:
            return
        try:
            await tools.send_event(
                content=json.dumps(
                    {ToolEventKey.NAME: tool_name, ToolEventKey.ARGS: input_data}
                ),
                message_type="tool_call",
            )
        except Exception as e:
            logger.warning("Failed to send tool_call event: %s", e)

    async def report_result(
        self,
        tools: AgentToolsProtocol,
        tool_name: str,
        result: Any,
        is_error: bool = False,
    ) -> None:
        if Emit.TOOL_CALLS not in self._features.emit:
            return
        try:
            await tools.send_event(
                content=json.dumps(
                    {
                        ToolEventKey.NAME: tool_name,
                        ToolEventKey.OUTPUT: result,
                        ToolEventKey.IS_ERROR: is_error,
                    }
                ),
                message_type="tool_result",
            )
        except Exception as e:
            logger.warning("Failed to send tool_result event: %s", e)


class NoopReporter:
    """Reporter that emits nothing — useful for adapters that report elsewhere."""

    async def report_call(
        self,
        tools: AgentToolsProtocol,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> None:
        return None

    async def report_result(
        self,
        tools: AgentToolsProtocol,
        tool_name: str,
        result: Any,
        is_error: bool = False,
    ) -> None:
        return None
