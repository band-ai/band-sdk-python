"""Core protocols for composition-based agent architecture."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar, runtime_checkable

from band_sdk_core import AgentFailure

from band.core.content import has_visible_content

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from anthropic.types import ToolParam

    from band.client.rest import (
        GetChatTaskHistoryResponse,
        ListAgentContactRequestsResponse,
        ListAgentContactsResponse,
        ListAgentMemoriesResponse,
        ListAgentPeersResponse,
        ListChatTasksResponse,
    )
    from band.core.task_types import (
        TaskAssignmentStatus,
        TaskLifecycleState,
        TaskListState,
    )
    from band.core.types import AgentInput, Capability
    from band.platform.event import PlatformEvent
    from band.runtime.execution import ExecutionContext
    from band.runtime.tools import ToolCallOutcome

T = TypeVar("T")

# Shared ``AgentFailure.code`` value for a stalled/unresponsive provider turn,
# so every adapter's timeout branch reports the same code instead of each
# retyping the literal.
FAILURE_CODE_TIMEOUT = "timeout"

# Shared generic message for a caught provider exception whose text must not
# reach the room (it can embed DB strings, paths, or tokens) -- the full
# detail goes to the agent log via logger.exception instead.
GENERIC_PROVIDER_FAILURE_MESSAGE = (
    "Internal error while processing message; see agent logs."
)


class TurnResultAlreadyReported(Exception):
    """A terminal turn failure that a nested handler already reported via
    ``send_failure``. An adapter's outer ``except`` re-raises this without
    reporting the same failure a second time."""


def to_failure_event(failure: AgentFailure) -> tuple[str, dict[str, Any]]:
    """Shared shape every ``send_failure`` implementation posts as an `error` event.

    A provider message can arrive blank (``Exception()`` and ``str("")`` both
    reach here empty). The platform rejects a blank chat event, so an
    unguarded blank message would make the failure vanish from the room
    entirely. The fallback string is part of the TS/Python parity contract —
    it must match ``toFailureEvent``'s exactly.
    """
    content = (
        failure.message.strip()
        if has_visible_content(failure.message)
        else f"{failure.provider} failed without an error message."
    )
    return content, {"failure": failure.to_dict()}


async def send_event_safe(
    tools: "AgentToolsProtocol",
    content: str,
    message_type: str,
    metadata: dict[str, Any] | None = None,
    *,
    log_label: str | None = None,
    log_level: int = logging.WARNING,
) -> bool:
    """Send a best-effort platform event, logging instead of raising on failure.

    For events whose loss is tolerable (a thought, a lifecycle/task marker),
    unlike a ``send_message`` call a caller depends on as a control signal.
    Returns whether the event was actually accepted, so a caller that only
    wants to update its own bookkeeping once delivery is confirmed (e.g.
    marking a session id persisted) can act on it.
    """
    try:
        await tools.send_event(
            content=content, message_type=message_type, metadata=metadata
        )
    except Exception:
        logger.log(
            log_level,
            "Failed to send %s event",
            log_label or message_type,
            exc_info=True,
        )
        return False
    return True


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

    async def send_failure(self, failure: AgentFailure) -> Any:
        """Report a provider-originated failure as a structured `error` event.

        Best-effort: swallows its own reporting failure rather than raising,
        so a caller reporting a failure never has that report replaced by an
        unrelated exception. Unlike ``send_event``, whose raising callers
        depend on it as a control signal.
        """
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

    @property
    def is_hub_room(self) -> bool:
        """True if this instance is bound to the contact hub room."""
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

    async def list_room_files(self, cursor: str | None = None) -> dict[str, Any]:
        """List files shared in the current room, paginated."""
        ...

    async def read_room_file(self, file_id: str) -> dict[str, Any]:
        """Read a file shared in the current room by id."""
        ...

    async def send_room_file(
        self,
        content: str,
        filename: str,
        caption: str = "",
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Upload text content as a file and share it in the current room."""
        ...

    def get_tool_schemas(
        self,
        format: str,
        *,
        capabilities: frozenset[Capability] | None = None,
    ) -> list[dict[str, Any]] | list["ToolParam"]:
        """Get tool schemas in provider-specific format (openai/anthropic)."""
        ...

    def get_anthropic_tool_schemas(
        self, *, capabilities: frozenset[Capability] | None = None
    ) -> list["ToolParam"]:
        """Get tool schemas in Anthropic format (strongly typed)."""
        ...

    def get_openai_tool_schemas(
        self, *, capabilities: frozenset[Capability] | None = None
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

    # Task board tools
    async def list_tasks(
        self,
        state: "TaskListState | None" = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> "ListChatTasksResponse":
        """List the shared tasks on this room's task board, in the Fern
        response envelope."""
        ...

    async def create_task(
        self,
        subject: str,
        detail: str | None = None,
        supersedes_id: str | None = None,
    ) -> Any:
        """Create a shared task on this room's task board."""
        ...

    async def get_task(self, id: str, include: Literal["history"] | None = None) -> Any:
        """Read one task by UUID or board number."""
        ...

    async def update_task(
        self,
        id: str,
        status: "TaskAssignmentStatus | None" = None,
        active_form: str | None = None,
        comment: str | None = None,
        subject: str | None = None,
        detail: str | None = None,
        state: "TaskLifecycleState | None" = None,
    ) -> Any:
        """Update a task's status, active_form, comment, subject, detail, or
        lifecycle state."""
        ...

    async def get_task_history(
        self, id: str, cursor: str | None = None, limit: int | None = None
    ) -> "GetChatTaskHistoryResponse":
        """The append-only history of one task, in the Fern response envelope."""
        ...

    async def get_board(self, include: Literal["history"] | None = None) -> Any:
        """Read this room's goal (the team mission)."""
        ...

    async def set_board(
        self, goal_title: str | None = None, goal_summary: str | None = None
    ) -> Any:
        """Set or update this room's goal (upsert)."""
        ...


@runtime_checkable
class FrameworkAdapter(Protocol):
    """
    Handles message processing for a specific LLM framework.

    CRITICAL: This adapter processes MESSAGES ONLY.

    The Preprocessor filters platform events:
    - MessageEvent → AgentInput → on_event()
    - RoomAddedEvent, ParticipantAdded, etc → FILTERED OUT (None)

    Participant changes are passed via `inp.participants_msg` (formatted string
    describing who joined/left). Adapters inject this into the LLM context.

    SDK users implement this for custom frameworks.
    SDK ships built-in adapters for LangGraph, Anthropic, etc.
    """

    async def on_event(self, inp: "AgentInput") -> None:
        """
        Process a user/system message.

        Args:
            inp: AgentInput with message, tools, history, participants_msg

        GUARANTEED: inp.msg is never from room lifecycle or presence events.
        """
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
