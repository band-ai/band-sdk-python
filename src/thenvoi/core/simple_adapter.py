"""Simple adapter base class for easy user DX."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Generic, TypeVar, cast

from thenvoi.core.protocols import AgentToolsProtocol, HistoryConverter
from thenvoi.core.types import (
    AdapterFeatures,
    AgentInput,
    Capability,
    Emit,
    PlatformMessage,
)

logger = logging.getLogger(__name__)

# Type variable for history type - bound by converter
H = TypeVar("H")


@dataclass(frozen=True, kw_only=True)
class ProviderUsageSnapshot:
    """Provider-owned usage for one model/API response."""

    source: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    api_call_count: int = 1
    cost_usd: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class SimpleAdapter(Generic[H], ABC):
    """
    Simple base class for framework adapters.

    Generic over H (history type) for full type safety.
    Users extend this and override on_message().

    Subclasses should declare SUPPORTED_EMIT and SUPPORTED_CAPABILITIES
    as class-level sets to document what they actually implement.
    on_started() will warn if features request unsupported values.

    Example:
        class MyAdapter(SimpleAdapter[list[ChatMessage]]):
            SUPPORTED_EMIT = frozenset({Emit.EXECUTION})
            SUPPORTED_CAPABILITIES = frozenset({Capability.MEMORY})

            def __init__(self):
                super().__init__(history_converter=MyHistoryConverter())

            async def on_message(
                self,
                msg: PlatformMessage,
                tools: AgentToolsProtocol,
                history: list[ChatMessage],  # Fully typed!
                participants_msg: str | None,
                contacts_msg: str | None,
                *,
                is_session_bootstrap: bool,
                room_id: str,
            ) -> None:
                ...
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset()
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset()

    def __init__(
        self,
        *,
        history_converter: HistoryConverter[H] | None = None,
        features: AdapterFeatures | None = None,
    ):
        """
        Initialize adapter.

        Args:
            history_converter: Optional converter for automatic history conversion.
                              Pass via __init__ to avoid shared state issues.
            features: Shared adapter feature settings (capabilities, emit, tool filters).
                     Defaults to empty AdapterFeatures().
        """
        self.history_converter = history_converter
        self.features = features or AdapterFeatures()
        self.agent_name: str = ""
        self.agent_description: str = ""
        self._provider_usage_snapshots: list[ProviderUsageSnapshot] = []

    def clear_provider_usage(self) -> None:
        """Clear provider usage snapshots recorded by previous model calls."""

        self._provider_usage_snapshots.clear()

    def provider_usage_snapshots(self) -> list[ProviderUsageSnapshot]:
        """Return provider-owned usage snapshots recorded by this adapter."""

        return list(self._provider_usage_snapshots)

    def _record_provider_usage(
        self,
        *,
        source: str,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None = None,
        api_call_count: int = 1,
        cost_usd: float | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        """Record provider-owned model/API usage when a framework exposes it."""

        if input_tokens is None or output_tokens is None:
            return
        if input_tokens < 0 or output_tokens < 0 or api_call_count <= 0:
            return
        resolved_total = total_tokens
        if resolved_total is None or resolved_total < input_tokens + output_tokens:
            resolved_total = input_tokens + output_tokens
        self._provider_usage_snapshots.append(
            ProviderUsageSnapshot(
                source=source,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=resolved_total,
                api_call_count=api_call_count,
                cost_usd=cost_usd,
                raw=raw or {},
            )
        )

    @abstractmethod
    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: H,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """
        Handle incoming message.

        Args:
            msg: Platform message
            tools: Agent tools (send_message, send_event, etc.)
            history: Converted history as type H
            participants_msg: Participants update message, or None
            contacts_msg: Contact changes broadcast message, or None
            is_session_bootstrap: True if adapter session is starting (first message from this room)
            room_id: The room identifier
        """
        ...

    async def on_cleanup(self, room_id: str) -> None:
        """Override for session cleanup."""
        pass

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Override for post-start setup."""
        self.agent_name = agent_name
        self.agent_description = agent_description

        # Warn on unsupported feature values
        unsupported_emit = self.features.emit - self.SUPPORTED_EMIT
        if unsupported_emit:
            logger.warning(
                "%s does not support emit values: %s (they will have no effect)",
                type(self).__name__,
                ", ".join(sorted(e.value for e in unsupported_emit)),
            )
        unsupported_caps = self.features.capabilities - self.SUPPORTED_CAPABILITIES
        if unsupported_caps:
            logger.warning(
                "%s does not support capability values: %s (they will have no effect)",
                type(self).__name__,
                ", ".join(sorted(c.value for c in unsupported_caps)),
            )

        # Propagate agent name to converter if it supports it
        if self.history_converter and hasattr(self.history_converter, "set_agent_name"):
            self.history_converter.set_agent_name(agent_name)

    # --- FrameworkAdapter protocol implementation ---

    async def on_event(self, inp: AgentInput) -> None:
        """Implements FrameworkAdapter.on_event()."""
        # Convert history if converter is set
        if self.history_converter:
            converted_history: Any = inp.history.convert(self.history_converter)
        else:
            # No converter: pass raw HistoryProvider as H
            # Adapters without converters should type as SimpleAdapter[HistoryProvider]
            converted_history = inp.history

        await self.on_message(
            msg=inp.msg,
            tools=inp.tools,
            history=cast("H", converted_history),
            participants_msg=inp.participants_msg,
            contacts_msg=inp.contacts_msg,
            is_session_bootstrap=inp.is_session_bootstrap,
            room_id=inp.room_id,
        )
