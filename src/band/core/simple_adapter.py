"""Simple adapter base class for easy user DX."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any, ClassVar, Generic, TypeVar, cast

from typing_extensions import Unpack

from band.client.rest import AsyncRestClient
from band.core.exceptions import BandConfigError
from band.core.protocols import AgentToolsProtocol, HistoryConverter
from band.core.types import (
    USAGE_EVENT_TYPE,
    USAGE_METADATA_KEY,
    AdapterFeatures,
    AgentInput,
    Capability,
    Emit,
    FeatureKwargs,
    PlatformConnection,
    PlatformMessage,
    TurnUsage,
)

logger = logging.getLogger(__name__)

# Type variable for history type - bound by converter
H = TypeVar("H")

_FlagT = TypeVar("_FlagT", Emit, Capability)


def _normalize_flags(
    value: "_FlagT | Iterable[_FlagT] | None",
    enum_cls: type[_FlagT],
) -> frozenset[_FlagT] | None:
    """Coerce a single member, an iterable, or ``None`` into a frozenset.

    ``None`` passes through (the caller decides what "not given" defaults
    to). A lone member must be checked with ``isinstance`` first --
    ``Emit``/``Capability`` are ``StrEnum``, so naively iterating one would
    walk its string characters instead of wrapping it.
    """
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return frozenset({value})
    try:
        return frozenset(enum_cls(v) for v in value)
    except ValueError as exc:
        raise BandConfigError(f"invalid {enum_cls.__name__} value: {exc}") from exc


def _describe(values: Iterable[Emit] | Iterable[Capability]) -> str:
    return ", ".join(sorted(v.value for v in values)) or "(none)"


class SimpleAdapter(Generic[H], ABC):
    """
    Simple base class for framework adapters.

    Generic over H (history type) for full type safety.
    Users extend this and override on_message().

    Subclasses should declare SUPPORTED_EMIT and SUPPORTED_CAPABILITIES
    as class-level sets to document what they actually implement.
    __init__() raises BandConfigError immediately for values outside them.

    Example:
        class MyAdapter(SimpleAdapter[list[ChatMessage]]):
            SUPPORTED_EMIT = frozenset({Emit.TOOL_CALLS})
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
        **features: Unpack[FeatureKwargs],
    ):
        """
        Initialize adapter.

        Args:
            history_converter: Optional converter for automatic history conversion.
                              Pass via __init__ to avoid shared state issues.
            **features: emit, capabilities, include_tools, exclude_tools,
                     include_categories -- see FeatureKwargs. ``emit`` defaults
                     to everything this adapter supports (SUPPORTED_EMIT) when
                     omitted; pass ``emit=()`` for silence. ``capabilities``
                     defaults to none (opt-in -- it puts extra tool schemas in
                     front of the model). Values outside SUPPORTED_EMIT /
                     SUPPORTED_CAPABILITIES raise BandConfigError immediately.
        """
        self.history_converter = history_converter
        self.agent_name: str = ""
        self.agent_description: str = ""
        # Injected by the runtime before on_started; adapters needing their own
        # platform access read it via require_platform().
        self.platform: PlatformConnection | None = None
        self.features = self._resolve_features(**features)

    def _resolve_features(
        self,
        *,
        emit: Emit | Iterable[Emit] | None = None,
        capabilities: Capability | Iterable[Capability] | None = None,
        include_tools: Iterable[str] | None = None,
        exclude_tools: Iterable[str] | None = None,
        include_categories: Iterable[str] | None = None,
    ) -> AdapterFeatures:
        """Normalize + validate constructor feature kwargs into AdapterFeatures.

        Split out from __init__ so a bridge that mirrors an inner adapter's
        SUPPORTED_EMIT/SUPPORTED_CAPABILITIES (e.g. SlackAdapter) can call this
        after shadowing those ClassVars on self, before super().__init__ runs.
        """
        resolved_emit = _normalize_flags(emit, Emit)
        if resolved_emit is None:
            resolved_emit = self.SUPPORTED_EMIT
        resolved_capabilities = _normalize_flags(capabilities, Capability)
        if resolved_capabilities is None:
            resolved_capabilities = frozenset()

        name = type(self).__name__
        if unsupported_emit := resolved_emit - self.SUPPORTED_EMIT:
            raise BandConfigError(
                f"{name} does not support emit kind(s): {_describe(unsupported_emit)}; "
                f"supported: {_describe(self.SUPPORTED_EMIT)}"
            )
        if unsupported_caps := resolved_capabilities - self.SUPPORTED_CAPABILITIES:
            raise BandConfigError(
                f"{name} does not support capability/-ies: {_describe(unsupported_caps)}; "
                f"supported: {_describe(self.SUPPORTED_CAPABILITIES)}"
            )

        return AdapterFeatures(
            emit=resolved_emit,
            capabilities=resolved_capabilities,
            include_tools=include_tools,
            exclude_tools=exclude_tools,
            include_categories=include_categories,
        )

    def require_platform(self) -> PlatformConnection:
        """The injected platform connection; raises before the agent starts."""
        if self.platform is None:
            raise RuntimeError(
                "platform connection not available yet; the runtime injects it "
                "when the Agent starts"
            )
        return self.platform

    def build_rest_client(self) -> AsyncRestClient:
        """A REST client for the injected platform connection.

        For a bridge adapter (Slack, A2A gateway, ACP server) that needs its
        own REST client: call this lazily in ``on_started``/on first use and
        cache the result, then expose it via `require_rest_client`.
        """
        connection = self.require_platform()
        return AsyncRestClient(base_url=connection.rest_url, api_key=connection.api_key)

    def require_rest_client(self, cached: AsyncRestClient | None) -> AsyncRestClient:
        """Return a lazily-built REST client; raises before the agent starts."""
        if cached is None:
            raise RuntimeError(
                "REST client not available yet; it is built when the Agent starts"
            )
        return cached

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

    async def emit_usage(self, tools: AgentToolsProtocol, usage: TurnUsage) -> None:
        """Emit a turn's token usage as a platform event, if enabled.

        Additive and best-effort, mirroring how adapters emit ``tool_call``
        events under ``Emit.TOOL_CALLS``: gated on ``Emit.USAGE`` in the
        adapter's features, and never allowed to crash the turn. The token
        counts ride an accepted ``task`` event's structured ``metadata`` under
        ``USAGE_METADATA_KEY`` (the read side filters on that key), since the
        backend rejects an unknown ``usage`` message_type today; see
        ``USAGE_EVENT_TYPE``.

        Adapters call this once per turn with the usage summed across the tool
        loop, typically from a ``finally`` so every exit path is covered: it
        never raises, and an empty total is skipped, so a turn that fails after
        a successful model call still reports the tokens it spent while a
        first-call failure emits nothing. When the calling task is being
        cancelled (shutdown, a turn timeout) the emit is skipped entirely, so
        teardown never blocks on network I/O and a CancelledError can't fire
        mid-send. An adapter that cannot observe usage (server-side execution)
        simply never calls it, so no event is emitted and the toolkit records
        N-A rather than a false zero. An all-zero usage is likewise skipped so
        a read never sees a zero-only record masquerading as real data.
        """
        if Emit.USAGE not in self.features.emit:
            return
        if usage.is_empty:
            logger.debug("Skipping empty usage event")
            return
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            logger.debug("Skipping usage emit during task cancellation")
            return
        try:
            await tools.send_event(
                content=(
                    f"Token usage: input={usage.input_tokens} "
                    f"output={usage.output_tokens}"
                ),
                message_type=USAGE_EVENT_TYPE,
                metadata={USAGE_METADATA_KEY: usage.to_dict()},
            )
        except Exception as e:  # best-effort: usage reporting must never crash a turn
            logger.warning("Failed to send usage event: %s", e)

    async def on_cleanup(self, room_id: str) -> None:
        """Override for session cleanup."""
        pass

    async def cleanup_all(self) -> None:
        """Override to release adapter-wide resources (clients, servers).

        Called by ``Agent.stop()`` after the runtime has stopped. Rooms are
        not individually cleaned on shutdown (``on_cleanup`` fires on room
        removal, not agent stop), so resources that outlive rooms — a CLI
        runtime subprocess, a self-hosted server, an external registration —
        release here.
        """
        pass

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Override for post-start setup."""
        self.agent_name = agent_name
        self.agent_description = agent_description

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
