"""Core types for composition-based agent architecture."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Any, Literal, TypeVar

if TYPE_CHECKING:
    from band.core.protocols import AgentToolsProtocol, HistoryConverter

T = TypeVar("T")


class MessageType(StrEnum):
    """Canonical ``message_type`` values used across platform history and events."""

    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    THOUGHT = "thought"
    ERROR = "error"
    TASK = "task"
    USAGE = "usage"


# Subset of message types accepted by ``band_send_event`` — the non-history
# event kinds. Derived from MessageType so the taxonomy stays single-sourced.
EventMessageType = Literal[MessageType.THOUGHT, MessageType.ERROR, MessageType.TASK]


class Capability(str, Enum):
    """Platform tool categories an adapter can expose to the LLM.

    These control tool-schema inclusion only -- they do NOT affect
    runtime event routing (WebSocket subscriptions, contact-event
    strategies, hub-room creation).  Those remain under
    ContactEventConfig / ContactEventStrategy in runtime/types.py.
    """

    MEMORY = "memory"
    CONTACTS = "contacts"


class Emit(str, Enum):
    """Event types an adapter can emit to the platform."""

    EXECUTION = "execution"
    THOUGHTS = "thoughts"
    TASK_EVENTS = "task_events"
    USAGE = "usage"


@dataclass(frozen=True)
class TurnUsage:
    """Token usage for a single agent turn, framework-agnostic.

    Each adapter maps its response object's usage fields onto these four
    dimensions (see the per-adapter table in the cost/token plan). A turn that
    makes several LLM calls (a tool loop) sums the per-call usage into one
    ``TurnUsage`` via ``+`` before emitting, so the record reflects the whole
    turn, not the last call.

    Zero is a valid value for any single dimension (a framework may not report
    it); ``is_empty`` is the "nothing was reported at all" signal that gates
    emission — an adapter that cannot observe usage never emits, and the toolkit
    records N-A rather than a misleading all-zero record.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: TurnUsage) -> TurnUsage:
        """Sum two per-call usages (used to aggregate across a tool loop)."""
        return TurnUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )

    @property
    def total_tokens(self) -> int:
        """Input + output tokens (cache fields are a subset breakdown of input,
        so they are not added again here)."""
        return self.input_tokens + self.output_tokens

    @property
    def is_empty(self) -> bool:
        """True when no dimension was reported — the signal to skip emission."""
        return not (
            self.input_tokens
            or self.output_tokens
            or self.cache_read_tokens
            or self.cache_write_tokens
        )

    def to_dict(self) -> dict[str, int]:
        """Serialize for the usage event payload (content JSON + metadata)."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


# Usage rides an already-accepted ``task`` event's free-form metadata (the path
# codex already proves) rather than a dedicated ``usage`` message_type: the
# backend's message_type whitelist rejects unknown types today, so a first-class
# ``usage`` type would need a platform change + deploy first. Emit and read both
# key off these two constants, so when the platform gains a ``usage`` type this
# is a one-line flip (``USAGE_EVENT_TYPE = MessageType.USAGE``) — the discriminator
# key is what a read filters on to tell a usage-bearing task event apart from a
# lifecycle one.
USAGE_EVENT_TYPE: MessageType = MessageType.TASK
USAGE_METADATA_KEY: str = "band_usage"


@dataclass(frozen=True)
class AdapterFeatures:
    """Shared adapter feature settings. Framework-agnostic knobs only.

    Custom tools are NOT included -- they are adapter-local because each
    framework has its own tool type.

    Accepts any iterable inputs for convenience; stores frozen types
    internally.
    """

    capabilities: frozenset[Capability]
    emit: frozenset[Emit]
    include_tools: tuple[str, ...] | None
    exclude_tools: tuple[str, ...] | None
    include_categories: tuple[str, ...] | None

    def __init__(
        self,
        *,
        capabilities: Iterable[Capability] = (),
        emit: Iterable[Emit] = (),
        include_tools: Iterable[str] | None = None,
        exclude_tools: Iterable[str] | None = None,
        include_categories: Iterable[str] | None = None,
    ) -> None:
        object.__setattr__(self, "capabilities", frozenset(capabilities))
        object.__setattr__(self, "emit", frozenset(emit))
        object.__setattr__(
            self,
            "include_tools",
            tuple(include_tools) if include_tools is not None else None,
        )
        object.__setattr__(
            self,
            "exclude_tools",
            tuple(exclude_tools) if exclude_tools is not None else None,
        )
        object.__setattr__(
            self,
            "include_categories",
            tuple(include_categories) if include_categories is not None else None,
        )


@dataclass(frozen=True)
class PlatformMessage:
    """Message from the platform."""

    id: str
    room_id: str
    content: str
    sender_id: str
    sender_type: str
    sender_name: str | None
    message_type: str
    metadata: Any  # Flexible - decoupled from transport layer schemas
    created_at: datetime

    def format_for_llm(self) -> str:
        """Format message for LLM consumption."""
        name = self.sender_name or self.sender_type or "Unknown"
        return f"[{name}]: {self.content}"


@dataclass(frozen=True)
class HistoryProvider:
    """
    Provides platform history with lazy conversion.

    Stores raw history, converts on-demand via converter.
    This avoids coupling to any specific framework.
    """

    raw: list[dict[str, Any]]

    def convert(self, converter: "HistoryConverter[T]") -> T:
        """
        Convert history using provided converter.

        Args:
            converter: Framework-specific converter

        Returns:
            History in framework-specific format
        """
        return converter.convert(self.raw)

    def __len__(self) -> int:
        return len(self.raw)

    def __bool__(self) -> bool:
        return bool(self.raw)


@dataclass(frozen=True)
class AgentInput:
    """
    Input to framework adapter.

    Contains everything an adapter needs to process a message.
    History is provided via HistoryProvider for lazy conversion.
    """

    msg: PlatformMessage
    tools: "AgentToolsProtocol"  # Protocol for testability (FakeAgentTools)
    history: HistoryProvider
    participants_msg: str | None
    contacts_msg: str | None  # Contact changes broadcast message
    is_session_bootstrap: bool
    room_id: str
