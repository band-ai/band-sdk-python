"""Core types for composition-based agent architecture."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Self, TypeVar

from typing_extensions import TypedDict

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


class ToolEventKey(StrEnum):
    """Canonical JSON keys for execution events written into room history."""

    NAME = "name"
    ARGS = "args"
    OUTPUT = "output"
    TOOL_CALL_ID = "tool_call_id"
    IS_ERROR = "is_error"


# Subset of message types accepted by ``band_send_event`` — the non-history
# event kinds. Derived from MessageType so the taxonomy stays single-sourced.
EventMessageType = Literal[MessageType.THOUGHT, MessageType.ERROR, MessageType.TASK]

# Status filter vocabulary shared by every list-contact-requests-family tool
# (master models and each adapter's own schema), so the choices have one
# definition instead of a hand-copied tuple per call site.
ContactRequestSentStatus = Literal[
    "pending", "approved", "rejected", "cancelled", "all"
]


class _FlagEnum(StrEnum):
    """A StrEnum whose members combine with ``|`` into a ``frozenset``.

    Unlike ``enum.Flag``, membership stays string-valued (no int bitmask),
    so a single member and a combined set both serialize/compare the same
    way everywhere else they are used (e.g. ``SUPPORTED_EMIT`` set algebra).
    """

    def __or__(self, other: "Self | frozenset[Self]") -> frozenset[Self]:
        # Only guards a member on at least one side of `|`. Two already-combined
        # frozensets of different _FlagEnum subclasses (e.g. `(Emit.A | Emit.B) |
        # (Capability.C | Capability.D)`) are both plain `frozenset` by then, so
        # `frozenset.__or__` runs instead and this guard never sees them.
        if isinstance(other, frozenset):
            combined = frozenset(other) | {self}
        elif isinstance(other, _FlagEnum):
            combined = frozenset({self, other})
        else:
            return NotImplemented
        if mismatched := {type(member) for member in combined} - {type(self)}:
            raise TypeError(
                f"cannot combine {type(self).__name__} with "
                f"{', '.join(sorted(t.__name__ for t in mismatched))}"
            )
        return combined

    __ror__ = __or__


class Capability(_FlagEnum):
    """Platform tool categories an adapter can expose to the LLM.

    These control tool-schema inclusion only -- they do NOT affect
    runtime event routing (WebSocket subscriptions, contact-event
    strategies, hub-room creation).  Those remain under
    ContactEventConfig / ContactEventStrategy in runtime/types.py.
    """

    MEMORY = "memory"
    CONTACTS = "contacts"


class Emit(_FlagEnum):
    """Event types an adapter can emit to the platform."""

    TOOL_CALLS = "tool_calls"
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

    # Convention: each field is the provider's *raw* reported value — no folding.
    # Whether cache is already counted inside ``input_tokens`` is provider-specific
    # (Anthropic/Claude SDK report input EXCLUDING cache; OpenAI/Gemini/LangChain
    # report input INCLUDING it; OpenCode reports cache as additive) — and a
    # single adapter can hit multiple providers, so no cross-provider "input always
    # includes cache" normalization is possible without per-provider logic. Treat
    # ``cache_read_tokens`` / ``cache_write_tokens`` as informational, and use
    # ``input_tokens + cache_read_tokens + cache_write_tokens`` as the robust
    # measure of total prompt size. (A first-class, provider-normalized cost model
    # is out of scope here; consumers have the raw fields.)

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
        """``input_tokens + output_tokens`` as raw-reported. Note this is NOT
        cache-normalized across providers (for providers that report cache
        separately from input it excludes cache); see the convention above."""
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
        """Serialize the four token counts for the usage event's metadata."""
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
        reasoning: str | None,
    ) -> TurnUsage:
        """Shared core of from_object/from_mapping: read the named fields via
        ``get`` and coerce each to a non-negative int. Values are passed through
        raw (no cache folding) per the class convention.

        ``reasoning`` is for providers that report reasoning/thinking tokens
        *disjointly* from output (codex, opencode, whose own totals are
        ``input + output + reasoning``): when named, it is folded into
        ``output_tokens`` so this four-field schema stays consistent with the
        providers that already count reasoning inside output (Anthropic thinking,
        OpenAI completion tokens). Leave it ``None`` when output already includes
        reasoning, to avoid double-counting."""
        return cls(
            input_tokens=_as_int(get(input)),
            output_tokens=_as_int(get(output))
            + (_as_int(get(reasoning)) if reasoning else 0),
            cache_read_tokens=_as_int(get(cache_read)) if cache_read else 0,
            cache_write_tokens=_as_int(get(cache_write)) if cache_write else 0,
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
        reasoning: str | None = None,
    ) -> TurnUsage:
        """Build from a usage *object*, reading the named attributes.

        The framework-specific attribute names are passed in; each is coerced to
        a non-negative int (missing/non-int → 0). ``src=None`` (usage absent on
        the response) yields an empty ``TurnUsage`` — so an adapter's mapper is a
        one-liner over ``getattr(response, "...", None)`` with no guard of its own.

        Pass ``reasoning`` only when the provider reports reasoning tokens
        disjointly from output (see :meth:`_build`); it is folded into
        ``output_tokens``.
        """
        if src is None:
            return cls()
        return cls._build(
            lambda name: getattr(src, name, 0),
            input=input,
            output=output,
            cache_read=cache_read,
            cache_write=cache_write,
            reasoning=reasoning,
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
        reasoning: str | None = None,
    ) -> TurnUsage:
        """Build from a usage *mapping* (dict), reading the named keys.

        The mapping-source twin of :meth:`from_object`; a non-mapping ``data``
        (e.g. usage absent) yields an empty ``TurnUsage``. Pass ``reasoning`` only
        when the provider reports reasoning tokens disjointly from output (see
        :meth:`_build`); it is folded into ``output_tokens``.
        """
        if not isinstance(data, Mapping):
            return cls()
        return cls._build(
            lambda name: data.get(name, 0),
            input=input,
            output=output,
            cache_read=cache_read,
            cache_write=cache_write,
            reasoning=reasoning,
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
    of re-deriving the ``band_usage`` check. It would be retired if usage ever
    became a first-class ``usage`` message_type."""
    return isinstance(metadata, Mapping) and USAGE_METADATA_KEY in metadata


@dataclass(frozen=True)
class PlatformConnection:
    """Band platform coordinates, injected into the adapter before ``on_started``.

    The runtime sets ``adapter.platform`` to this once credentials are known, so
    an adapter that needs its own platform access (e.g. a bridge building an
    ``AsyncRestClient``) reads it from here instead of asking for ``api_key`` /
    ``rest_url`` constructor parameters the caller already gave the Agent.
    """

    agent_id: str
    api_key: str
    rest_url: str
    ws_url: str


class FeatureKwargs(TypedDict, total=False):
    """The feature keywords every ``SimpleAdapter`` constructor accepts.

    Adapters forward these via ``**features: Unpack[FeatureKwargs]`` instead
    of repeating the five parameters in every signature, and instead of
    taking a wrapping ``AdapterFeatures`` object -- callers pass the knobs
    directly, e.g. ``ClaudeSDKAdapter(model="...", emit=Emit.THOUGHTS)``.
    ``AdapterFeatures`` itself is the internal frozen container ``self.features``
    resolves to; it is not part of the public constructor surface.
    """

    emit: Emit | Iterable[Emit]
    capabilities: Capability | Iterable[Capability]
    include_tools: Iterable[str]
    exclude_tools: Iterable[str]
    include_categories: Iterable[str]


@dataclass(frozen=True)
class AdapterFeatures:
    """Shared adapter feature settings. Framework-agnostic knobs only.

    Custom tools are NOT included -- they are adapter-local because each
    framework has its own tool type.

    Accepts any iterable inputs for convenience; stores frozen types
    internally. Internal container only -- ``SimpleAdapter.__init__``
    builds this from ``FeatureKwargs``; adapters do not take it directly.
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
