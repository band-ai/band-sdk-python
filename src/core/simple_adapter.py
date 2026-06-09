"""Simple adapter base class for easy user DX."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, TypeVar, cast

from band.core.protocols import AgentToolsProtocol, HistoryConverter
from band.core.types import (
    AdapterFeatures,
    AgentInput,
    Capability,
    Emit,
    PlatformMessage,
)

logger = logging.getLogger(__name__)

# Type variable for history type - bound by converter
H = TypeVar("H")


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
