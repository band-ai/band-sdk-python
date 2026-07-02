"""Core types for composition-based agent architecture."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
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


def _as_int(value: object) -> int:
    """Coerce a usage field to an int; anything non-int (None, missing) → 0."""
    return value if isinstance(value, int) else 0


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

    # Cross-provider convention (adapters MUST map onto this so the schema means
    # one thing everywhere): ``input_tokens`` is the *total* prompt the model
    # processed, INCLUDING cached tokens; ``cache_read_tokens`` / ``cache_write_tokens``
    # are a *subset breakdown* of that total, not additive on top of it. So
    # ``total_tokens = input_tokens + output_tokens`` is correct for every adapter,
    # and cost math never double-counts (or under-counts) cache. Providers whose
    # native "input" excludes cache (Anthropic, Claude SDK) fold cache back into
    # ``input_tokens`` in their mapper; providers whose native "input" already
    # includes cache (Gemini/ADK, LiteLLM-based crewai, LangChain) pass it through.

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
        """Total tokens for the turn: input (which already includes cache, per the
        convention above) + output. Cache fields are a subset of input, so they
        are not added again."""
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

    @classmethod
    def _build(
        cls,
        get: Callable[[str], object],
        *,
        input: str,
        output: str,
        cache_read: str | None,
        cache_write: str | None,
        cache_in_input: bool,
    ) -> TurnUsage:
        """Shared core of from_object/from_mapping: read the named fields via
        ``get`` and apply the cache convention.

        ``cache_in_input`` declares the provider's native shape: True when the
        input field already includes cached tokens (Gemini/ADK, LiteLLM, LangChain
        — the default), False when it excludes them (Anthropic, Claude SDK), in
        which case cache is folded back into ``input_tokens`` so the schema always
        means "input = total prompt incl. cache" (see the class convention).
        """
        cache_read_tokens = _as_int(get(cache_read)) if cache_read else 0
        cache_write_tokens = _as_int(get(cache_write)) if cache_write else 0
        input_tokens = _as_int(get(input))
        if not cache_in_input:
            input_tokens += cache_read_tokens + cache_write_tokens
        return cls(
            input_tokens=input_tokens,
            output_tokens=_as_int(get(output)),
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )

    @classmethod
    def from_object(
        cls,
        src: object,
        *,
        input: str,
        output: str,
        cache_read: str | None = None,
        cache_write: str | None = None,
        cache_in_input: bool = True,
    ) -> TurnUsage:
        """Build from a usage *object*, reading the named attributes.

        The framework-specific attribute names are passed in; each is coerced to
        a non-negative int (missing/non-int → 0). ``src=None`` (usage absent on
        the response) yields an empty ``TurnUsage`` — so an adapter's mapper is a
        one-liner over ``getattr(response, "...", None)`` with no guard of its own.
        See :meth:`_build` for ``cache_in_input``.
        """
        if src is None:
            return cls()
        return cls._build(
            lambda name: getattr(src, name, 0),
            input=input,
            output=output,
            cache_read=cache_read,
            cache_write=cache_write,
            cache_in_input=cache_in_input,
        )

    @classmethod
    def from_mapping(
        cls,
        data: object,
        *,
        input: str,
        output: str,
        cache_read: str | None = None,
        cache_write: str | None = None,
        cache_in_input: bool = True,
    ) -> TurnUsage:
        """Build from a usage *mapping* (dict), reading the named keys.

        The mapping-source twin of :meth:`from_object`; a non-mapping ``data``
        (e.g. usage absent) yields an empty ``TurnUsage``. See :meth:`_build` for
        ``cache_in_input``.
        """
        if not isinstance(data, Mapping):
            return cls()
        return cls._build(
            lambda name: data.get(name, 0),
            input=input,
            output=output,
            cache_read=cache_read,
            cache_write=cache_write,
            cache_in_input=cache_in_input,
        )


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


def is_usage_event(metadata: object) -> bool:
    """Whether an event's ``metadata`` marks it as a usage record (see
    ``SimpleAdapter.emit_usage``).

    Because usage currently rides ``USAGE_EVENT_TYPE`` (a ``task`` event) rather
    than a dedicated type, every ``task``-event consumer that should NOT treat
    usage as a lifecycle task calls this to skip it — the single source of truth
    for "is this a usage event", so a new consumer has one guard to reuse instead
    of re-deriving the ``band_usage`` check. Retired once usage becomes a
    first-class ``usage`` message_type (see INT-933)."""
    return isinstance(metadata, Mapping) and USAGE_METADATA_KEY in metadata


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
