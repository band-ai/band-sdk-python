"""
Parlant adapter using the official Parlant SDK directly.

This adapter integrates the Parlant framework (https://github.com/emcie-co/parlant)
with the Thenvoi platform.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import ClassVar, TYPE_CHECKING, Any

from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from thenvoi.converters.parlant import ParlantHistoryConverter, ParlantMessages
from thenvoi.integrations.parlant.tools import (
    create_parlant_tools,
    set_session_tools,
    was_message_sent,
)
from thenvoi.runtime.custom_tools import CustomToolDef
from thenvoi.runtime.prompts import render_system_prompt

if TYPE_CHECKING:
    import parlant.sdk as p  # type: ignore[missing-import]
    from parlant.core.application import Application  # type: ignore[missing-import]
    from parlant.core.sessions import SessionId  # type: ignore[missing-import]

logger = logging.getLogger(__name__)


# Parlant preamble message tag - used to identify acknowledgment messages before tool execution
PARLANT_PREAMBLE_TAG = "__preamble__"

# Platform tools already create user-visible Band effects directly.
_SILENT_REPORTING_TOOLS = frozenset({"thenvoi_send_message", "thenvoi_send_event"})


class ParlantAdapter(SimpleAdapter[ParlantMessages]):
    """
    Parlant adapter using the official Parlant SDK directly.

    This adapter integrates directly with the Parlant engine for message processing.

    Example:
        import parlant.sdk as p

        async with p.Server() as server:
            agent = await server.create_agent(
                name="Assistant",
                description="A helpful assistant",
            )

            adapter = ParlantAdapter(
                server=server,
                parlant_agent=agent,
            )

            thenvoi_agent = Agent.create(
                adapter=adapter,
                agent_id="...",
                api_key="...",
            )
            await thenvoi_agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.EXECUTION})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        server: p.Server,
        parlant_agent: p.Agent,
        system_prompt: str | None = None,
        custom_section: str | None = None,
        history_converter: ParlantHistoryConverter | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        features: AdapterFeatures | None = None,
    ):
        """
        Initialize the Parlant SDK adapter.

        Args:
            server: The Parlant SDK Server instance
            parlant_agent: The Parlant Agent instance
            system_prompt: Full system prompt override
            custom_section: Custom instructions appended to agent description
            history_converter: Custom history converter (optional)
            additional_tools: CustomToolDef tuples exposed as Parlant tools
            features: Shared adapter feature settings (capabilities, emit, tool filters).
        """
        self._features_explicitly_provided = features is not None
        super().__init__(
            history_converter=history_converter or ParlantHistoryConverter(),
            features=features,
        )

        self._server = server
        self._parlant_agent = parlant_agent
        self.system_prompt = system_prompt
        self.custom_section = custom_section
        self._custom_tools = additional_tools or []

        # Parlant application (accessed via container)
        self._app: Application | None = None

        # Per-room session mapping (room_id -> parlant session_id)
        self._room_sessions: dict[str, SessionId] = {}

        # Per-room customer mapping (room_id -> parlant customer_id)
        self._room_customers: dict[str, str] = {}

        # Rendered system prompt (set after start)
        self._system_prompt: str = ""

        # Adapter-installed Parlant guideline that carries the Band platform contract.
        self._contract_guideline_installed = False
        self._contract_guideline_id: str | None = None

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Initialize after agent metadata is fetched."""
        await super().on_started(agent_name, agent_description)

        # Render system prompt
        self._system_prompt = self.system_prompt or render_system_prompt(
            agent_name=agent_name,
            agent_description=agent_description,
            custom_section=self.custom_section or "",
            features=self.features,
        )

        # Get Application from Parlant container
        try:
            from parlant.core.application import Application  # type: ignore[missing-import]

            self._app = self._server.container[Application]
            await self._install_thenvoi_contract_guideline()
            logger.info(
                "Parlant SDK adapter started for agent: %s (parlant_agent_id=%s)",
                agent_name,
                self._parlant_agent.id,
            )
        except Exception as e:
            logger.error("Failed to get Parlant Application: %s", e, exc_info=True)
            raise

    async def _install_thenvoi_contract_guideline(self) -> None:
        """Install the rendered Band platform contract into Parlant."""
        if self._contract_guideline_installed:
            return

        import parlant.sdk as p  # type: ignore[missing-import]

        prompt_hash = hashlib.sha256(self._system_prompt.encode("utf-8")).hexdigest()
        tools = create_parlant_tools(
            self.features,
            legacy_defaults=not self._features_explicitly_provided,
            additional_tools=self._custom_tools,
        )
        guideline = await self._parlant_agent.create_guideline(
            condition="Any incoming Band room message is being handled",
            action=(
                "Follow the Band platform instructions in this guideline. "
                "Communicate through thenvoi_send_message with the intended "
                "recipient in mentions; use Band tools for participants, "
                "contacts, and memory when those tools are available. Treat the "
                "initial @mention as the trigger target, not automatically as "
                "the reply recipient."
            ),
            description=self._system_prompt,
            matcher=p.MATCH_ALWAYS,
            tools=tools,
            metadata={
                "thenvoi_adapter_contract": True,
                "prompt_hash": prompt_hash,
            },
        )
        self._contract_guideline_id = str(guideline.id)
        self._contract_guideline_installed = True

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: ParlantMessages,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """
        Handle incoming message using the Parlant SDK directly.

        Uses Parlant's internal Application for session and message management.
        """
        logger.debug("Handling message %s in room %s", msg.id, room_id)

        if not self._app:
            error = "Parlant Application not initialized"
            logger.error(error)
            await self._report_error(tools, error)
            raise RuntimeError(error)

        app = self._app
        sender_name = msg.sender_name or msg.sender_id or "User"

        # Get or create Parlant session for this room (need session_id first)
        try:
            session_id = await self._get_or_create_session(room_id, sender_name)
        except Exception as e:
            logger.error("Failed to get/create session for room %s: %s", room_id, e)
            await self._report_error(tools, f"Session initialization failed: {e}")
            raise
        session_id_str = str(session_id)

        # Set tools for this session (keyed by session_id for cross-task access)
        set_session_tools(session_id_str, tools)
        logger.info("Room %s: Set tools for session_id=%s", room_id, session_id_str)

        # On bootstrap, inject historical context
        if is_session_bootstrap and history:
            injected = await self._inject_history(session_id, history)
            logger.info("Room %s: Injected %s messages from history", room_id, injected)

        # Build user message, prepending updates if present
        user_message = msg.format_for_llm()
        if participants_msg:
            user_message = f"[System Update]: {participants_msg}\n\n{user_message}"
            logger.info("Room %s: Included participants update in message", room_id)
        if contacts_msg:
            user_message = f"[System Update]: {contacts_msg}\n\n{user_message}"
            logger.info("Room %s: Included contacts broadcast in message", room_id)
        logger.info(
            "Room %s: Sending message to Parlant: %s...",
            room_id,
            user_message[:100],
        )

        try:
            from parlant.core.app_modules.sessions import Moderation  # type: ignore[missing-import]
            from parlant.core.sessions import EventSource  # type: ignore[missing-import]

            # Create customer message event (triggers processing)
            logger.info("Room %s: Creating customer message event...", room_id)
            event = await app.sessions.create_customer_message(
                session_id=session_id,
                moderation=Moderation.NONE,
                message=user_message,
                source=EventSource.CUSTOMER,
                trigger_processing=True,
                metadata=None,
            )
            logger.info(
                "Room %s: Customer message created, offset=%s",
                room_id,
                event.offset,
            )

            # Wait for and process agent response
            await self._process_agent_response(
                session_id=session_id,
                room_id=room_id,
                min_offset=event.offset,
                tools=tools,
                sender_name=sender_name,
            )

        except Exception as e:
            logger.error("Error processing message: %s", e, exc_info=True)
            await self._report_error(tools, str(e))
            raise
        finally:
            # Clear tools after message processing
            set_session_tools(session_id_str, None)
            logger.info(
                "Room %s: Cleared tools for session_id=%s",
                room_id,
                session_id_str,
            )

        logger.debug("Message %s processed successfully", msg.id)

    async def _get_or_create_session(
        self,
        room_id: str,
        customer_name: str,
    ) -> SessionId:
        """Get existing session for room or create a new one."""
        if room_id in self._room_sessions:
            return self._room_sessions[room_id]

        if not self._app:
            raise RuntimeError("Parlant Application not initialized")

        app = self._app
        logger.info("Creating Parlant session for room: %s", room_id)

        # Create or get customer
        customer_id = await self._get_or_create_customer(room_id, customer_name)

        # Create session
        session = await app.sessions.create(
            customer_id=customer_id,
            agent_id=self._parlant_agent.id,
            title=f"Thenvoi Room {room_id[:8]}",
        )

        self._room_sessions[room_id] = session.id
        logger.info("Session created: %s for room %s", session.id, room_id)

        return session.id

    async def _get_or_create_customer(
        self,
        room_id: str,
        customer_name: str,
    ) -> Any:
        """Get or create a Parlant customer."""
        if room_id in self._room_customers:
            return self._room_customers[room_id]

        # Create customer via server
        customer = await self._server.create_customer(
            name=customer_name,
            id=f"thenvoi-{room_id[:8]}",
        )

        self._room_customers[room_id] = customer.id
        return customer.id

    async def _inject_history(
        self,
        session_id: SessionId,
        history: ParlantMessages,
    ) -> int:
        """Inject historical messages into a Parlant session.

        Only injects COMPLETE exchanges (user message + assistant response).
        User messages without a following assistant response are NOT injected,
        as they represent pending/unanswered questions that should be handled
        by the current message flow.
        """
        if not self._app:
            return 0

        if not history:
            return 0

        app = self._app
        from parlant.core.app_modules.sessions import Moderation  # type: ignore[missing-import]
        from parlant.core.sessions import EventKind, EventSource  # type: ignore[missing-import]

        # First, filter to only complete exchanges
        # A user message is only injected if it has a following assistant response
        complete_history: ParlantMessages = []
        i = 0
        while i < len(history):
            msg = history[i]
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "user" and content:
                # Check if there's a following assistant response
                if i + 1 < len(history) and history[i + 1].get("role") == "assistant":
                    # Complete exchange - include both
                    complete_history.append(msg)
                    complete_history.append(history[i + 1])
                    i += 2
                else:
                    # User message without response - skip (it's pending)
                    logger.debug(
                        "Skipping unanswered user message: %s...", content[:50]
                    )
                    i += 1
            elif role == "assistant" and content:
                # Standalone assistant message (rare) - include it
                complete_history.append(msg)
                i += 1
            else:
                i += 1

        # Now inject the filtered history
        count = 0
        for hist in complete_history:
            role = hist.get("role", "user")
            content = hist.get("content", "")

            if not content:
                continue

            try:
                if role == "user":
                    await app.sessions.create_customer_message(
                        session_id=session_id,
                        moderation=Moderation.NONE,
                        message=content,
                        source=EventSource.CUSTOMER,
                        trigger_processing=False,
                        metadata={"historical": True},
                    )
                    count += 1
                elif role == "assistant":
                    # Parlant requires participant info for AI_AGENT messages
                    sender = hist.get("sender", self.agent_name or "Assistant")
                    await app.sessions.create_event(
                        session_id=session_id,
                        kind=EventKind.MESSAGE,
                        source=EventSource.AI_AGENT,
                        data={
                            "message": content,
                            "participant": {"display_name": sender},
                        },
                        metadata={"historical": True},
                        trigger_processing=False,
                    )
                    count += 1
            except Exception as e:
                logger.warning("Failed to inject history message (%s): %s", role, e)

        return count

    @staticmethod
    def _mapping_value(value: Any, key: str, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(key, default)
        return getattr(value, key, default)

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if isinstance(value, list):
            return [ParlantAdapter._jsonable(item) for item in value]
        if isinstance(value, Mapping):
            return {
                str(key): ParlantAdapter._jsonable(item) for key, item in value.items()
            }
        return value

    @staticmethod
    def _tool_name_from_id(tool_id: Any) -> str:
        raw_tool_id = str(tool_id or "unknown")
        return raw_tool_id.split(":", 1)[-1]

    async def _report_tool_event(self, event: Any, tools: AgentToolsProtocol) -> None:
        """Forward Parlant-observed tool calls as Band execution events."""
        data = self._mapping_value(event, "data", {})
        tool_calls = self._mapping_value(data, "tool_calls", []) or []
        event_id = self._mapping_value(event, "id", None)
        event_offset = self._mapping_value(event, "offset", "unknown")

        for index, tool_call in enumerate(tool_calls):
            tool_name = self._tool_name_from_id(
                self._mapping_value(tool_call, "tool_id", None)
            )
            if tool_name in _SILENT_REPORTING_TOOLS:
                continue

            arguments = self._mapping_value(tool_call, "arguments", {}) or {}
            result = self._mapping_value(tool_call, "result", {}) or {}
            output = self._mapping_value(result, "data", result)
            tool_call_id = f"parlant-{event_id or event_offset}-{index}"

            try:
                await tools.send_event(
                    content=json.dumps(
                        {
                            "name": tool_name,
                            "args": self._jsonable(arguments),
                            "tool_call_id": tool_call_id,
                        },
                        default=str,
                    ),
                    message_type="tool_call",
                )
                await tools.send_event(
                    content=json.dumps(
                        {
                            "name": tool_name,
                            "output": self._jsonable(output),
                            "tool_call_id": tool_call_id,
                        },
                        default=str,
                    ),
                    message_type="tool_result",
                )
            except Exception as error:
                logger.warning("Failed to report Parlant tool execution: %s", error)

    async def _process_agent_response(
        self,
        session_id: SessionId,
        room_id: str,
        min_offset: int,
        tools: AgentToolsProtocol,
        sender_name: str,
    ) -> None:
        """Wait for and process agent response events."""
        if not self._app:
            raise RuntimeError(f"Room {room_id}: No Parlant Application available")

        app = self._app
        session_id_str = str(session_id)
        from parlant.core.async_utils import Timeout  # type: ignore[missing-import]
        from parlant.core.sessions import EventKind, EventSource  # type: ignore[missing-import]

        event_kinds = [EventKind.MESSAGE]
        event_source = EventSource.AI_AGENT
        if Emit.EXECUTION in self.features.emit:
            event_kinds.append(EventKind.TOOL)
            event_source = None

        current_offset = min_offset
        max_iterations = 10

        for iteration in range(1, max_iterations + 1):
            logger.info(
                "Room %s: Waiting for agent response (min_offset=%s, iteration=%s)...",
                room_id,
                current_offset + 1,
                iteration,
            )

            try:
                has_update = await app.sessions.wait_for_more_events(  # pyrefly: ignore[missing-attribute]
                    session_id=session_id,
                    min_offset=current_offset + 1,
                    kinds=event_kinds,
                    source=event_source,
                    timeout=Timeout(120),
                )
            except Exception as e:
                if was_message_sent(session_id_str):
                    logger.info(
                        "Room %s: Message was sent via tool before wait error", room_id
                    )
                    return
                raise RuntimeError(f"Room {room_id}: Error waiting for response") from e

            if not has_update:
                if was_message_sent(session_id_str):
                    logger.info("Room %s: Timeout after successful tool send", room_id)
                    return
                raise TimeoutError(
                    f"Room {room_id}: Timeout waiting for agent response"
                )

            try:
                events = await app.sessions.find_events(
                    session_id=session_id,
                    min_offset=current_offset + 1,
                    source=event_source,
                    kinds=event_kinds,
                    trace_id=None,
                )
            except Exception as e:
                if was_message_sent(session_id_str):
                    logger.info(
                        "Room %s: Message was sent via tool before event lookup error",
                        room_id,
                    )
                    return
                raise RuntimeError(
                    f"Room {room_id}: Error finding response events"
                ) from e

            if not events:
                if was_message_sent(session_id_str):
                    return
                raise RuntimeError(
                    f"Room {room_id}: No response events found despite update signal"
                )

            delivered = False
            saw_non_preamble = False
            saw_relevant_event = False

            for event in events:
                logger.debug(
                    "Room %s: Event kind=%s, source=%s, data=%s",
                    room_id,
                    event.kind,
                    event.source,
                    event.data,
                )

                if hasattr(event, "offset") and event.offset > current_offset:
                    current_offset = event.offset

                if event.kind == EventKind.TOOL:
                    saw_relevant_event = True
                    await self._report_tool_event(event, tools)
                    continue

                if (
                    event.kind != EventKind.MESSAGE
                    or event.source != EventSource.AI_AGENT
                ):
                    continue

                saw_relevant_event = True
                data = event.data
                message_content = ""
                tags: list[str] = []

                if isinstance(data, dict):
                    message_content = str(data.get("message", ""))
                    raw_tags = data.get("tags", [])
                    if isinstance(raw_tags, list):
                        tags = [str(tag) for tag in raw_tags]
                elif isinstance(data, str):
                    message_content = data

                if PARLANT_PREAMBLE_TAG in tags:
                    logger.info(
                        "Room %s: Skipping preamble message: %s...",
                        room_id,
                        message_content[:50],
                    )
                    continue

                saw_non_preamble = True

                if was_message_sent(session_id_str):
                    logger.info(
                        "Room %s: Message already sent via tool, skipping Parlant response: %s...",
                        room_id,
                        message_content[:50],
                    )
                    delivered = True
                    continue

                if not message_content:
                    raise RuntimeError(
                        f"Room {room_id}: Empty final message content from Parlant"
                    )

                await tools.send_message(message_content, mentions=[sender_name])
                logger.info("Room %s: Message sent successfully", room_id)
                delivered = True

            if delivered or was_message_sent(session_id_str):
                logger.info("Room %s: Response delivery confirmed", room_id)
                return

            if saw_non_preamble:
                raise RuntimeError(
                    f"Room {room_id}: Parlant produced a response but nothing was delivered"
                )

            if saw_relevant_event:
                logger.info(
                    "Room %s: Only got non-final events, continuing to wait for final message...",
                    room_id,
                )
            else:
                logger.info(
                    "Room %s: No relevant agent events found, continuing to wait...",
                    room_id,
                )

        if was_message_sent(session_id_str):
            logger.info("Room %s: Max iterations after successful tool send", room_id)
            return
        raise TimeoutError(
            f"Room {room_id}: Reached max iterations ({max_iterations}) waiting for response"
        )

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up session when agent leaves a room."""
        if room_id in self._room_sessions:
            del self._room_sessions[room_id]
        if room_id in self._room_customers:
            del self._room_customers[room_id]

        logger.debug("Room %s: Cleaned up Parlant session", room_id)

    async def _report_error(self, tools: AgentToolsProtocol, error: str) -> None:
        """Send error event (best effort)."""
        try:
            await tools.send_event(content=f"Error: {error}", message_type="error")
        except Exception:
            pass

    async def cleanup_all(self) -> None:
        """Cleanup all sessions (call on stop)."""
        self._room_sessions.clear()
        self._room_customers.clear()
        logger.info("Parlant adapter cleanup complete")
