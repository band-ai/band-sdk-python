"""A2A adapter that forwards messages to a remote A2A agent."""

from __future__ import annotations

import logging
from typing import ClassVar

import httpx
from a2a.client import Client, ClientConfig, ClientFactory
from a2a.types import SendMessageRequest
from a2a.types import (
    Message as A2AMessage,
    Role,
    SubscribeToTaskRequest,
    StreamResponse,
    Task,
    TaskState,
)

from band.converters.a2a import A2AHistoryConverter
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from band.integrations.a2a.protocol import (
    TERMINAL_TASK_STATES,
    state_name,
    text_from_message,
    text_message,
)
from band.integrations.a2a.types import A2AAuth, A2ASessionState

logger = logging.getLogger(__name__)


class A2AAdapter(SimpleAdapter[A2ASessionState]):
    """Adapter that forwards messages to a remote A2A agent.

    This adapter enables remote A2A-compliant agents to participate in Band
    chat rooms as peers. Messages from the Band platform are forwarded to
    the A2A agent, and responses are posted back to the chat.

    The adapter uses A2A's native context management - each Band room maps
    to an A2A context_id, allowing the remote agent to maintain conversation
    state without history being resent each time.

    Session state (context_id, task_id, task_state) is persisted via task
    events in platform history, allowing sessions to be restored when the
    agent rejoins a chat room.

    Example:
        from band import Agent
        from band.integrations.a2a import A2AAdapter

        adapter = A2AAdapter(
            remote_url="https://currency-agent.example.com",
            streaming=True,
        )
        agent = Agent.create(
            adapter=adapter,
            agent_id="currency-bot",
            api_key="...",
        )
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset()
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset()

    def __init__(
        self,
        remote_url: str,
        auth: A2AAuth | None = None,
        streaming: bool = True,
        features: AdapterFeatures | None = None,
    ) -> None:
        """Initialize A2A adapter.

        Args:
            remote_url: Base URL of the remote A2A agent.
            auth: Optional authentication configuration.
            streaming: Whether to use streaming mode (SSE) for responses.
        """
        super().__init__(
            history_converter=A2AHistoryConverter(),
            features=features,
        )
        self.remote_url = remote_url
        self.auth = auth
        self.streaming = streaming
        self._client: Client | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._contexts: dict[str, str] = {}  # room_id → A2A context_id
        self._tasks: dict[str, str] = {}  # room_id → last task_id
        self._task_cache: dict[tuple[str, str], Task] = {}
        # Track sender per task for mentions: (room_id, task_id) → sender info
        self._task_senders: dict[tuple[str, str], dict[str, str]] = {}

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Initialize A2A client connection."""
        await super().on_started(agent_name, agent_description)

        headers = self.auth.to_headers() if self.auth else {}

        self._http_client = httpx.AsyncClient(headers=headers)
        factory = ClientFactory(
            ClientConfig(streaming=self.streaming, httpx_client=self._http_client)
        )
        self._client = await factory.create_from_url(self.remote_url)

        logger.info(
            "Connected to A2A agent at %s (streaming=%s, auth=%s)",
            self.remote_url,
            self.streaming,
            bool(headers),
        )

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: A2ASessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Forward message to A2A agent, post response to Band."""
        if self._client is None:
            raise RuntimeError("A2A client not initialized. Call on_started first.")

        logger.debug(
            "on_message: room_id=%s, is_session_bootstrap=%s, history=%s",
            room_id,
            is_session_bootstrap,
            history,
        )

        # Rehydrate session state on bootstrap
        if is_session_bootstrap and history:
            await self._rehydrate_from_history(room_id, history)

        # Convert Band message to A2A format
        a2a_message = self._to_a2a_message(msg, room_id)

        try:
            # Send to remote A2A agent and process events
            async for event in self._client.send_message(
                SendMessageRequest(message=a2a_message)
            ):
                await self._handle_event(
                    event, tools, room_id, msg.sender_id, msg.sender_name
                )

        except Exception as e:
            logger.exception("A2A agent error: %s", e)
            await tools.send_event(
                content=f"A2A agent error: {e}",
                message_type="error",
                metadata={"a2a_error": str(e)},
            )

    async def _handle_event(
        self,
        event: StreamResponse,
        tools: AgentToolsProtocol,
        room_id: str,
        sender_id: str,
        sender_name: str | None,
    ) -> None:
        """Handle A2A event and forward to Band platform."""
        if getattr(event, "HasField", lambda _name: False)("message"):
            text = text_from_message(event.message)
            if text:
                await tools.send_message(
                    content=text,
                    mentions=[{"id": sender_id, "name": sender_name or ""}],
                )
            return

        if event.HasField("task"):
            task = event.task
            self._task_cache[(room_id, task.id)] = Task()
            self._task_cache[(room_id, task.id)].CopyFrom(task)
        elif event.HasField("status_update"):
            update = event.status_update
            task = self._task_cache.setdefault(
                (room_id, update.task_id),
                Task(id=update.task_id, context_id=update.context_id),
            )
            task.status.CopyFrom(update.status)
        elif event.HasField("artifact_update"):
            update = event.artifact_update
            task = self._task_cache.setdefault(
                (room_id, update.task_id),
                Task(id=update.task_id, context_id=update.context_id),
            )
            task.artifacts.add().CopyFrom(update.artifact)
        else:
            return

        key = (room_id, task.id)

        # Store sender info on first event for this task
        if key not in self._task_senders:
            self._task_senders[key] = {"id": sender_id, "name": sender_name or ""}

        # Track task/context for multi-turn and resumption
        self._tasks[room_id] = task.id
        if task.context_id:
            self._contexts[room_id] = task.context_id

        state = task.status.state

        try:
            # Handle based on task state
            if state == TaskState.TASK_STATE_WORKING:
                # Stream progress as thought event (no mentions needed for events)
                status_text = self._get_status_text(task)
                if status_text:
                    await tools.send_event(content=status_text, message_type="thought")

            elif state == TaskState.TASK_STATE_INPUT_REQUIRED:
                # Agent needs more info - send as message with mention
                text = self._get_status_text(task) or "Please provide more information."
                sender = self._task_senders.get(key)
                await tools.send_message(
                    content=text,
                    mentions=[sender] if sender else None,
                )
                # Emit task event for rehydration (input_required is resumable)
                await self._emit_task_event(tools, task, state)

            elif state == TaskState.TASK_STATE_COMPLETED:
                # Extract and send final response with mention
                response = self._extract_response(task)
                if response:
                    sender = self._task_senders.get(key)
                    await tools.send_message(
                        content=response,
                        mentions=[sender] if sender else None,
                    )

            elif state in (
                TaskState.TASK_STATE_FAILED,
                TaskState.TASK_STATE_CANCELED,
                TaskState.TASK_STATE_REJECTED,
                TaskState.TASK_STATE_AUTH_REQUIRED,
            ):
                # Error states - send as error event (no mentions needed)
                error_text = self._get_status_text(task) or f"Task {state_name(state)}"
                await tools.send_event(
                    content=error_text,
                    message_type="error",
                    metadata={"a2a_state": state_name(state)},
                )
        finally:
            # Clean up on terminal states
            if state in TERMINAL_TASK_STATES:
                # Emit task event for rehydration (records final state)
                await self._emit_task_event(tools, task, state)
                # Clean up sender tracking
                self._task_senders.pop(key, None)
                self._task_cache.pop(key, None)
                # Clear task_id so next message starts a new task
                # (context_id is preserved for multi-turn conversation)
                self._tasks.pop(room_id, None)

    def _to_a2a_message(self, msg: PlatformMessage, room_id: str) -> A2AMessage:
        """Convert Band message to A2A format."""
        context_id = self._contexts.get(room_id)
        task_id = self._tasks.get(room_id)
        logger.debug(
            "_to_a2a_message: room_id=%s, context_id=%s, task_id=%s",
            room_id,
            context_id,
            task_id,
        )
        return text_message(
            msg.content,
            role=Role.ROLE_USER,
            context_id=context_id,
            task_id=task_id,
        )

    def _get_status_text(self, task: Task) -> str | None:
        """Extract text from task status message."""
        if task.status.message:
            return text_from_message(task.status.message)
        return None

    def _extract_response(self, task: Task) -> str:
        """Extract final response text from A2A Task.

        Checks in order:
        1. Artifacts (primary response container)
        2. Status message
        3. Last agent message in history
        """
        # First: check artifacts
        if task.artifacts:
            for artifact in task.artifacts:
                for part in artifact.parts:
                    if part.text:
                        return part.text

        # Fallback: check status message
        if task.status.message:
            text = text_from_message(task.status.message)
            if text:
                return text

        # Last resort: check history for last agent message
        if task.history:
            for msg in reversed(task.history):
                if msg.role == Role.ROLE_AGENT:
                    text = text_from_message(msg)
                    if text:
                        return text

        return ""

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up A2A context for room."""
        self._contexts.pop(room_id, None)
        self._tasks.pop(room_id, None)
        # Clean up any task_senders entries for this room
        keys_to_remove = [key for key in self._task_senders if key[0] == room_id]
        for key in keys_to_remove:
            self._task_senders.pop(key, None)
            self._task_cache.pop(key, None)
        logger.debug("Cleaned up A2A context for room %s", room_id)

    async def _emit_task_event(
        self, tools: AgentToolsProtocol, task: Task, state: TaskState
    ) -> None:
        """Emit a task event to persist A2A session state in platform history.

        This enables session rehydration when the agent rejoins a chat room.

        Args:
            tools: Agent tools for sending events.
            task: The A2A task.
            state: Current task state.
        """
        await tools.send_event(
            content=f"A2A task {state_name(state)}",
            message_type="task",
            metadata={
                "a2a_context_id": task.context_id,
                "a2a_task_id": task.id,
                "a2a_task_state": state_name(state),
            },
        )

    async def _rehydrate_from_history(
        self, room_id: str, state: A2ASessionState
    ) -> None:
        """Restore A2A session state from platform history.

        Called on session bootstrap to restore context_id and optionally
        resume a task that was in input_required state.

        Args:
            room_id: The room ID.
            state: Session state extracted from history.
        """
        # Restore context for conversation continuity
        if state.context_id:
            self._contexts[room_id] = state.context_id
            logger.info(
                "Restored A2A context_id %s for room %s", state.context_id, room_id
            )

        # Try to resume task if it was in a resumable state
        if state.task_id and state.task_state not in {
            state_name(value) for value in TERMINAL_TASK_STATES
        }:
            await self._try_resubscribe(room_id, state.task_id)

    async def _try_resubscribe(self, room_id: str, task_id: str) -> None:
        """Try to reconnect to an A2A task.

        Uses A2A's resubscribe API to check if a task is still active
        and resume receiving events from it.

        Args:
            room_id: The room ID.
            task_id: The task ID to resubscribe to.
        """
        if not self._client:
            return

        try:
            async for event in self._client.subscribe(
                SubscribeToTaskRequest(id=task_id)
            ):
                if event.HasField("task"):
                    task = event.task
                elif event.HasField("status_update"):
                    update = event.status_update
                    task = self._task_cache.setdefault(
                        (room_id, update.task_id),
                        Task(id=update.task_id, context_id=update.context_id),
                    )
                    task.status.CopyFrom(update.status)
                else:
                    continue

                current_state = task.status.state
                if current_state not in TERMINAL_TASK_STATES:
                    self._tasks[room_id] = task_id
                    if task.context_id:
                        self._contexts[room_id] = task.context_id
                    logger.info(
                        "Resumed A2A task %s (state=%s)",
                        task_id,
                        state_name(current_state),
                    )
                else:
                    logger.info(
                        "A2A task %s already terminal (state=%s)",
                        task_id,
                        state_name(current_state),
                    )
                break  # Only need first event to get current state
        except Exception as e:
            logger.warning("Could not resubscribe to A2A task %s: %s", task_id, e)
