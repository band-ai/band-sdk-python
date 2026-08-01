"""Core protocols: FrameworkAdapter, tools, providers, gateways."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, Self, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from band.core.turn.history import SessionHistoryPolicy
    from anthropic.types import ToolParam

    from band.client.rest import (
        ListAgentContactRequestsResponse,
        ListAgentContactsResponse,
        ListAgentMemoriesResponse,
        ListAgentPeersResponse,
    )
    from band.core.contracts import (
        ModelRequest,
        ModelResponse,
        TurnEvent,
    )
    from band.core.types import AgentInput
    from band.platform.event import PlatformEvent
    from band.runtime.execution import ExecutionContext
    from band.runtime.tools import ToolCallOutcome

T = TypeVar("T")


@runtime_checkable
class HistoryConverter(Protocol[T]):
    """
    Converts raw platform history to framework-specific format.

    SDK users implement this for custom frameworks.
    SDK ships built-in converters for LangGraph, Anthropic, etc.
    """

    def convert(self, raw: list[dict[str, Any]]) -> T:
        """
        Convert raw platform history to framework format.

        Args:
            raw: Platform history from format_history_for_llm()
                 Each dict has: role, content, sender_name, sender_type, message_type

        Returns:
            Framework-specific history type
        """
        ...


@runtime_checkable
class AgentToolsProtocol(Protocol):
    """
    Interface for Band platform tools.

    Enables:
    - Testable adapters via fake implementations
    - Type-safe contracts for custom implementations
    - Clear documentation of tool methods

    Implementations: AgentTools (default), FakeAgentTools (testing)
    """

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> Any:
        """Send a message to the chat room."""
        ...

    async def send_event(
        self,
        content: str,
        message_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Send an event (tool_call, tool_result, thought, error, task)."""
        ...

    async def add_participant(self, identifier: str, role: str = "member") -> Any:
        """Add a participant to the current room by handle, name, or ID."""
        ...

    async def remove_participant(self, identifier: str) -> Any:
        """Remove a participant from the current room by handle, name, or ID."""
        ...

    @property
    def participants(self) -> list[Any]:
        """Read-only snapshot of cached room participants."""
        ...

    async def get_participants(self) -> Any:
        """Get participants in the current room."""
        ...

    async def lookup_peers(
        self, page: int = 1, page_size: int = 50
    ) -> ListAgentPeersResponse:
        """Find available peers, in the Fern response envelope."""
        ...

    async def create_chatroom(self, task_id: str | None = None) -> str:
        """Create a new chat room."""
        ...

    async def fetch_room_context(
        self,
        *,
        room_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Fetch room context for state-reconstruction use cases.

        Returns the platform's agent-context payload: messages this agent sent
        or messages mentioning this agent, paginated, oldest first.
        Implementations route through the platform REST surface; wrappers
        (audit, rate limiting, PII redaction) intercept here. Response shape:
        ``{"data": [<message dict>...], "meta": {...}}``.
        """
        ...

    def get_tool_schemas(
        self,
        format: str,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list[dict[str, Any]] | list["ToolParam"]:
        """Get tool schemas in provider-specific format (openai/anthropic)."""
        ...

    def get_anthropic_tool_schemas(
        self, *, include_memory: bool = False, include_contacts: bool = True
    ) -> list["ToolParam"]:
        """Get tool schemas in Anthropic format (strongly typed)."""
        ...

    def get_openai_tool_schemas(
        self, *, include_memory: bool = False, include_contacts: bool = True
    ) -> list[dict[str, Any]]:
        """Get tool schemas in OpenAI format (strongly typed)."""
        ...

    async def execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool call by name with validated arguments."""
        ...

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        """Execute a tool call, returning a structured outcome (value + ``ok`` flag).

        Prefer this over :meth:`execute_tool_call` when the caller must branch on
        success/failure: a base tool that fails without raising (bad args, API error)
        reports it via ``ok=False`` rather than a raised exception, and the plain
        variant discards that signal.
        """
        ...

    # Contact management tools
    async def list_contacts(
        self, page: int = 1, page_size: int = 50
    ) -> ListAgentContactsResponse:
        """List agent's contacts, in the Fern response envelope."""
        ...

    async def add_contact(self, handle: str, message: str | None = None) -> Any:
        """Send a contact request to add someone as a contact."""
        ...

    async def remove_contact(
        self, handle: str | None = None, contact_id: str | None = None
    ) -> Any:
        """Remove an existing contact by handle or ID."""
        ...

    async def list_contact_requests(
        self,
        page: int = 1,
        page_size: int = 50,
        sent_status: str = "pending",
    ) -> ListAgentContactRequestsResponse:
        """List received and sent contact requests, in the Fern envelope."""
        ...

    async def respond_contact_request(
        self,
        action: str,
        handle: str | None = None,
        request_id: str | None = None,
    ) -> Any:
        """Respond to a contact request (approve, reject, or cancel)."""
        ...

    # Memory management tools (enterprise only)
    async def list_memories(
        self,
        subject_id: str | None = None,
        scope: str | None = None,
        system: str | None = None,
        type: str | None = None,
        segment: str | None = None,
        content_query: str | None = None,
        page_size: int = 50,
        status: str | None = None,
    ) -> ListAgentMemoriesResponse:
        """List memories accessible to the agent, in the Fern response envelope."""
        ...

    async def store_memory(
        self,
        content: str,
        system: str,
        type: str,
        segment: str,
        thought: str,
        scope: str,
        subject_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Store a new memory entry."""
        ...

    async def get_memory(self, memory_id: str) -> Any:
        """Retrieve a specific memory by ID."""
        ...

    async def supersede_memory(self, memory_id: str) -> Any:
        """Mark a memory as superseded (soft delete)."""
        ...

    async def archive_memory(self, memory_id: str) -> Any:
        """Archive a memory (hide but preserve)."""
        ...


@runtime_checkable
class FrameworkAdapter(Protocol):
    """Adapter contract: one turn entrypoint.

    ``Agent`` (and ``run_adapter_turn``) call ``handle_turn`` once per
    inbound message. Implement this protocol directly, or extend
    ``SimpleAdapter`` and override ``on_message`` (history conversion stays
    in the base).

    CRITICAL: processes MESSAGES ONLY. The preprocessor filters platform
    events — ``MessageEvent`` becomes ``AgentInput``; room/participant
    lifecycle events do not reach the adapter. Participant changes arrive
    as ``inp.participants_msg`` for the LLM context.
    """

    async def handle_turn(self, inp: "AgentInput") -> None:
        """Run one turn for ``inp`` (never a room-lifecycle event)."""
        ...

    async def on_cleanup(self, room_id: str) -> None:
        """
        Clean up session state for a room.

        Args:
            room_id: Room being cleaned up
        """
        ...

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """
        Called after platform runtime starts.

        Args:
            agent_name: Agent name from platform
            agent_description: Agent description from platform
        """
        ...


@runtime_checkable
class Preprocessor(Protocol):
    """
    Converts platform events to AgentInput.

    Most users use DefaultPreprocessor.
    Power users can implement custom preprocessing.

    Note: PlatformEvent is a tagged union type:
        PlatformEvent = MessageEvent | RoomAddedEvent | RoomRemovedEvent | ...

    Use pattern matching for type-safe event handling:
        match event:
            case MessageEvent(payload=msg):
                ...  # msg is MessageCreatedPayload (typed)
    """

    async def process(
        self,
        ctx: "ExecutionContext",
        event: "PlatformEvent",
        agent_id: str,
    ) -> "AgentInput | None":
        """
        Process platform event into AgentInput.

        Args:
            ctx: Execution context for this room
            event: Tagged union event (MessageEvent | RoomAddedEvent | ...)
            agent_id: Current agent's ID (for self-message filtering)

        Returns:
            AgentInput if event should be processed, None to skip
        """
        ...


# ---------------------------------------------------------------------------
# ModelProvider / Gateway contracts
# ---------------------------------------------------------------------------


@runtime_checkable
class EventSink(Protocol):
    """Runtime-owned sink for causally ordered turn events.

    Assigns the envelope (``run_id``, sequence, timestamp) and fans out to observers.
    """

    async def emit(self, event: TurnEvent) -> None:
        """Emit one turn event."""
        ...


@runtime_checkable
class CancellationToken(Protocol):
    """View over ``ExecutionContext.interrupt()`` — no parallel cancel mechanism."""

    @property
    def cancelled(self) -> bool:
        """True once the runtime has signalled interrupt/stop for this run."""
        ...

    def throw_if_cancelled(self) -> None:
        """Raise ``asyncio.CancelledError`` if cancelled."""
        ...


@runtime_checkable
class RunContext(Protocol):
    """Per-run context for one adapter turn."""

    @property
    def tools(self) -> AgentToolsProtocol: ...

    @property
    def events(self) -> EventSink: ...

    @property
    def cancellation(self) -> CancellationToken: ...



@runtime_checkable
class ModelProvider(Protocol):
    """LLM request/response translation. Owns its SDK client."""

    async def complete(
        self, request: ModelRequest, *, context: ModelContext
    ) -> ModelResponse: ...

    def default_history_policy(self) -> SessionHistoryPolicy:
        """Session history shape this provider expects for the native tool loop."""
        ...


@runtime_checkable
class ModelContext(Protocol):
    """Per-call context for ``ModelProvider.complete``."""

    @property
    def cancellation(self) -> CancellationToken: ...


@runtime_checkable
class Gateway(Protocol):
    """Owns credentials, transport, and exclusive agent lifecycle.

    Receives a constructed-but-not-started ``Agent``. Passing an already-started
    agent, or the same agent to two gateways, is an error.
    """

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def serve(self) -> None: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> bool | None: ...
