"""
Parlant adapter using the official Parlant SDK directly.

This adapter integrates the Parlant framework (https://github.com/emcie-co/parlant)
with the Band platform.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import ClassVar, TYPE_CHECKING, Any

from typing_extensions import Unpack

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import Capability, Emit, FeatureKwargs, PlatformMessage
from band.integrations.parlant.server import running_parlant_server
from band.integrations.parlant.tools import (
    create_parlant_tools,
    set_session_tools,
    was_message_sent,
)
from band.converters.parlant import ParlantHistoryConverter, ParlantMessages

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    import parlant.sdk as p  # type: ignore[missing-import]
    from parlant.core.application import Application  # type: ignore[missing-import]
    from parlant.core.sessions import SessionId  # type: ignore[missing-import]

logger = logging.getLogger(__name__)


# Parlant preamble message tag - used to identify acknowledgment messages before tool execution
PARLANT_PREAMBLE_TAG = "__preamble__"
EMPTY_READ_BACKOFF_SECONDS = 0.05

# Called in on_started with the live (server, parlant_agent) for anything the
# declarative surface doesn't cover (journeys, guideline dependencies, ...).
ConfigureCallback = Callable[["p.Server", "p.Agent"], Awaitable[None]]


@dataclass(frozen=True)
class GuidelineSpec:
    """A guideline declared before startup, created on the live agent at start.

    ``tools=None`` means "attach the Band platform tools" — the common case.
    An explicit sequence (including ``[]``) is passed through verbatim.
    """

    condition: str | None
    action: str | None
    tools: Sequence[Any] | None
    kwargs: dict[str, Any] = field(default_factory=dict)


class ParlantAdapter(SimpleAdapter[ParlantMessages]):
    """
    Parlant adapter using the official Parlant SDK directly.

    The adapter owns the Parlant server lifecycle: it reserves free ports, boots
    ``p.Server`` when the Band agent starts, and tears it down when the agent
    stops. Guidelines are declared up front with :meth:`add_guideline` and created
    on the live agent at startup, with the Band platform tools attached by default.

    Example:
        import parlant.sdk as p
        from band import Agent
        from band.adapters import ParlantAdapter

        adapter = ParlantAdapter(
            name="Assistant",
            description="A helpful assistant",
            nlp_service=p.NLPServices.openai,
        )
        adapter.add_guideline(
            condition="User asks a question",
            action="Answer via band_send_message, mentioning the user",
        )

        band_agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await band_agent.run()

    Escape hatches:
        * ``configure=`` — async callback run at startup with the live
          ``(server, parlant_agent)`` for full native Parlant API access
          (journeys, guideline dependencies, canned responses, ...).
        * ``server=`` / ``parlant_agent=`` — bring your own running server (and
          optionally your own agent on it). Borrowed objects are never torn down
          by the adapter.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset()
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.FILES}
    )

    def __init__(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        nlp_service: Any | None = None,
        server_options: dict[str, Any] | None = None,
        server: p.Server | None = None,
        parlant_agent: p.Agent | None = None,
        configure: ConfigureCallback | None = None,
        system_prompt: str | None = None,
        custom_section: str | None = None,
        history_converter: ParlantHistoryConverter | None = None,
        response_timeout: float = 300.0,
        response_poll: float = 30.0,
        **features: Unpack[FeatureKwargs],
    ):
        """
        Initialize the Parlant SDK adapter.

        Args:
            name: Parlant agent name. Defaults to the Band agent's name.
            description: Parlant agent description (its behavioral instructions).
                Defaults to the Band agent's description.
            nlp_service: Parlant NLP service for the adapter-owned server (e.g.
                ``p.NLPServices.openai``). Defaults to Parlant's own default.
            server_options: Extra keyword arguments passed verbatim to
                ``p.Server(...)`` for the adapter-owned server (``host``,
                ``session_store``, ``log_level``, ...). ``port`` /
                ``tool_service_port`` default to freshly reserved free ports.
            server: Bring your own running ``p.Server`` instead of an
                adapter-owned one. Borrowed: the adapter never tears it down.
                Mutually exclusive with ``nlp_service`` / ``server_options``.
            parlant_agent: Bring your own ``p.Agent``; requires ``server=``.
            configure: Async callback ``(server, parlant_agent)`` run at startup
                after guidelines are applied, for full native Parlant API access.
            system_prompt: Full override of the created Parlant agent's
                description (its behavioral instructions). Only applies to an
                adapter-created agent; cannot be combined with ``parlant_agent=``.
            custom_section: Extra instructions appended to the created agent's
                description. Ignored when ``system_prompt`` overrides the whole
                description; cannot be combined with ``parlant_agent=``.
            history_converter: Custom history converter (optional)
            response_timeout: Max seconds to wait for the agent's response per turn.
                Default 300 (5 min): a cold start — server warmup plus the first
                guideline-matching/generation round-trips — can run long on a slow host.
            response_poll: Seconds per polling window within that budget (default 30);
                the wait returns as soon as the response arrives, so a warm turn is fast.
        """
        super().__init__(
            history_converter=history_converter or ParlantHistoryConverter(),
            **features,
        )

        if response_timeout <= 0:
            raise ValueError("response_timeout must be greater than 0")
        if response_poll <= 0:
            raise ValueError("response_poll must be greater than 0")
        if parlant_agent is not None and server is None:
            raise ValueError(
                "parlant_agent requires the server it lives on; pass server= as well"
            )
        if server is not None and (nlp_service is not None or server_options):
            raise ValueError(
                "nlp_service/server_options configure the adapter-owned server; "
                "they cannot be combined with a caller-provided server="
            )
        if parlant_agent is not None and (
            system_prompt is not None or custom_section is not None
        ):
            raise ValueError(
                "system_prompt/custom_section shape the adapter-created agent's "
                "description; they cannot be applied to a caller-provided "
                "parlant_agent="
            )

        self._name = name
        self._description = description
        self._nlp_service = nlp_service
        self._server_options = dict(server_options or {})
        self._server = server
        self._owns_server = server is None
        self._server_cm: AbstractAsyncContextManager[p.Server] | None = None
        self._parlant_agent = parlant_agent
        self._created_agent = parlant_agent is None
        self._configure = configure
        self.system_prompt = system_prompt
        self.custom_section = custom_section
        self._response_timeout = response_timeout
        self._response_poll = response_poll

        # Guidelines declared before startup, created on the live agent at start.
        # A restart with a borrowed (still-alive) agent must only create the
        # specs appended since the last create pass, not the whole list again;
        # a restart that got a fresh agent (owned server) needs all of them.
        # This count tracks how many leading specs already exist on the
        # current self._parlant_agent, so it resets alongside that agent.
        self._guideline_specs: list[GuidelineSpec] = []
        self._guidelines_applied_count = 0

        # Band platform tools as Parlant ToolEntry objects (built at start)
        self._tools: list[Any] = []

        self._started = False

        # Parlant application (accessed via container)
        self._app: Application | None = None

        # Per-room session mapping (room_id -> parlant session_id)
        self._room_sessions: dict[str, SessionId] = {}

        # Per-room customer mapping (room_id -> parlant customer_id)
        self._room_customers: dict[str, str] = {}

    def add_guideline(
        self,
        condition: str | None = None,
        action: str | None = None,
        *,
        tools: Sequence[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Declare a guideline, created on the live Parlant agent at startup.

        Mirrors ``parlant.sdk.Agent.create_guideline``; extra keyword arguments
        are forwarded to it verbatim. ``tools`` defaults to the Band platform
        tools; pass an explicit sequence (including ``[]``) to override.

        For live guideline management (return values, dependencies), use the
        ``configure=`` callback instead.
        """
        if self._started:
            raise RuntimeError(
                "add_guideline must be called before the agent starts; use the "
                "configure= callback or adapter.parlant_agent.create_guideline() "
                "for a running agent"
            )
        self._guideline_specs.append(GuidelineSpec(condition, action, tools, kwargs))

    @property
    def server(self) -> p.Server:
        """The running Parlant server (available once the Band agent starts)."""
        if self._server is None:
            raise RuntimeError(
                "Parlant server not running yet; it starts with the agent"
            )
        return self._server

    @property
    def parlant_agent(self) -> p.Agent:
        """The Parlant agent (available once the Band agent starts)."""
        if self._parlant_agent is None:
            raise RuntimeError(
                "Parlant agent not created yet; it starts with the agent"
            )
        return self._parlant_agent

    @property
    def tools(self) -> list[Any]:
        """Band platform tools as Parlant ToolEntry objects (built at startup)."""
        return list(self._tools)

    def _agent_instructions(self, agent_description: str) -> str:
        """Behavioral instructions for the adapter-created Parlant agent."""
        if self.system_prompt:
            return self.system_prompt
        description = self._description or agent_description
        if self.custom_section:
            description = f"{description}\n\n{self.custom_section}"
        return description

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Boot the Parlant server (unless borrowed) and configure the agent."""
        await super().on_started(agent_name, agent_description)

        # A failure below must release the owned server: Agent.start() only runs
        # adapter cleanup for failures *after* on_started, not inside it.
        try:
            if self._server is None:
                options = dict(self._server_options)
                if self._nlp_service is not None:
                    options["nlp_service"] = self._nlp_service

                prepared_agent: p.Agent | None = None
                prepared_app: Application | None = None

                async def setup(server: p.Server) -> None:
                    nonlocal prepared_agent, prepared_app
                    prepared_agent, prepared_app = await self._prepare_server(
                        server, agent_name, agent_description
                    )

                server_cm = running_parlant_server(setup=setup, **options)
                server = await server_cm.__aenter__()
                self._server_cm = server_cm
                assert prepared_agent is not None and prepared_app is not None
                self._server = server
                self._parlant_agent = prepared_agent
                self._app = prepared_app
            else:
                self._parlant_agent, self._app = await self._prepare_server(
                    self._server, agent_name, agent_description
                )
        except BaseException:
            await self._release_server()
            if self._owns_server:
                # The context manager owns cleanup even when its __aenter__ fails,
                # but it is only retained after a successful enter.
                self._server = None
            if self._parlant_agent is None:
                # A retried start() must redo every guideline against whatever
                # agent it creates next; the count only survives a failure
                # alongside the agent it was applied to.
                self._guidelines_applied_count = 0
            raise

        self._started = True
        logger.info(
            "Parlant SDK adapter started for agent: %s (parlant_agent_id=%s)",
            agent_name,
            self._parlant_agent.id,
        )

    async def _prepare_server(
        self,
        server: p.Server,
        agent_name: str,
        agent_description: str,
    ) -> tuple[p.Agent, Application]:
        """Declare everything Parlant must process before its setup phase."""
        agent = self._parlant_agent
        if agent is None:
            agent = await server.create_agent(
                name=self._name or agent_name,
                description=self._agent_instructions(agent_description),
            )

        self._tools = create_parlant_tools(self.features)
        for spec in self._guideline_specs[self._guidelines_applied_count :]:
            await agent.create_guideline(
                condition=spec.condition,
                action=spec.action,
                tools=self._tools if spec.tools is None else spec.tools,
                **spec.kwargs,
            )
            # Each successful create is a retry checkpoint. A later failure leaves
            # no sibling tasks running and never duplicates this guideline.
            self._guidelines_applied_count += 1

        if self._configure is not None:
            await self._configure(server, agent)

        from parlant.core.application import Application  # type: ignore[missing-import]  # noqa: PLC0415 -- genuinely deferred; parlant extra kept out of this module's unconditional import surface

        return agent, server.container[Application]

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
            logger.error("Parlant Application not initialized")
            return

        app = self._app
        sender_name = msg.sender_name or msg.sender_id or "User"

        # Get or create Parlant session for this room (need session_id first)
        try:
            session_id = await self._get_or_create_session(room_id, sender_name)
        except Exception as e:
            logger.error("Failed to get/create session for room %s: %s", room_id, e)
            await self._report_error(tools, f"Session initialization failed: {e}")
            return
        session_id_str = str(session_id)

        # Set tools for this session (keyed by session_id for cross-task access)
        set_session_tools(session_id_str, tools)
        logger.debug("Room %s: Set tools for session_id=%s", room_id, session_id_str)

        # On bootstrap, inject historical context
        if is_session_bootstrap and history:
            injected = await self._inject_history(session_id, history)
            logger.info("Room %s: Injected %s messages from history", room_id, injected)

        # Build user message, prepending updates if present
        user_message = msg.format_for_llm()
        if participants_msg:
            user_message = f"[System Update]: {participants_msg}\n\n{user_message}"
            logger.debug("Room %s: Included participants update in message", room_id)
        if contacts_msg:
            user_message = f"[System Update]: {contacts_msg}\n\n{user_message}"
            logger.debug("Room %s: Included contacts broadcast in message", room_id)
        logger.debug(
            "Room %s: Sending message to Parlant: %s...",
            room_id,
            user_message[:100],
        )

        try:
            from parlant.core.app_modules.sessions import Moderation  # type: ignore[missing-import]  # noqa: PLC0415 -- genuinely deferred; parlant extra kept out of this module's unconditional import surface
            from parlant.core.sessions import EventSource  # type: ignore[missing-import]  # noqa: PLC0415 -- genuinely deferred; parlant extra kept out of this module's unconditional import surface

            # Create customer message event (triggers processing)
            logger.debug("Room %s: Creating customer message event...", room_id)
            event = await app.sessions.create_customer_message(
                session_id=session_id,
                moderation=Moderation.NONE,
                message=user_message,
                source=EventSource.CUSTOMER,
                trigger_processing=True,
                metadata=None,
            )
            logger.debug(
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
            logger.debug(
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
        logger.debug("Creating Parlant session for room: %s", room_id)

        # Create or get customer
        customer_id = await self._get_or_create_customer(room_id, customer_name)

        # Create session
        session = await app.sessions.create(
            customer_id=customer_id,
            agent_id=self.parlant_agent.id,
            title=f"Band Room {room_id[:8]}",
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

        # Create customer via server. Uses the full room_id (not a truncated
        # prefix) since this is a stable per-room identity key on Parlant's
        # server — a short prefix risks two Band rooms colliding onto the
        # same Parlant customer.
        customer = await self.server.create_customer(
            name=customer_name,
            id=f"band-{room_id}",
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
        from parlant.core.app_modules.sessions import Moderation  # type: ignore[missing-import]  # noqa: PLC0415 -- genuinely deferred; parlant extra kept out of this module's unconditional import surface
        from parlant.core.sessions import EventKind, EventSource  # type: ignore[missing-import]  # noqa: PLC0415 -- genuinely deferred; parlant extra kept out of this module's unconditional import surface

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

    async def _process_agent_response(
        self,
        session_id: SessionId,
        room_id: str,
        min_offset: int,
        tools: AgentToolsProtocol,
        sender_name: str,
    ) -> None:
        """Wait for and process agent response events.

        Parlant may send multiple messages:
        1. A preamble message (tagged with __preamble__) - acknowledgment before tool execution
        2. Final message(s) after tool execution

        If the send_message tool was called during processing, we don't need to
        forward Parlant's response (it would be a duplicate or empty).

        Waiting is bounded by a total budget, polling in shorter windows and
        retrying on an empty window so a slow (cold-start) turn is still answered.
        If the budget elapses with no final message, the turn is given up honestly
        (no reply): a preamble alone is an acknowledgment, not an answer, so it is
        never forwarded as the reply. Parlant intermittently stalls the post-preamble
        generation; when it does, this turn legitimately produces nothing.
        """
        if not self._app:
            logger.error("Room %s: No Parlant Application available", room_id)
            return

        app = self._app
        session_id_str = str(session_id)
        from parlant.core.async_utils import Timeout  # type: ignore[missing-import]  # noqa: PLC0415 -- genuinely deferred; parlant extra kept out of this module's unconditional import surface
        from parlant.core.sessions import EventKind, EventSource  # type: ignore[missing-import]  # noqa: PLC0415 -- genuinely deferred; parlant extra kept out of this module's unconditional import surface

        current_offset = min_offset
        # Wait up to the total response budget, polling in shorter windows. An empty
        # window is not a give-up: the agent may still be generating (cold start /
        # slow model), so we keep waiting until the budget is spent. The loop returns
        # the moment a final (or tool-sent) message is seen, so a warm turn is fast.
        # Use perf_counter (the highest-resolution monotonic clock) for deadlines —
        # its resolution holds up on Windows, where time.monotonic() is coarse.
        deadline = time.perf_counter() + self._response_timeout

        while time.perf_counter() < deadline:
            poll = min(self._response_poll, deadline - time.perf_counter())

            # Wait for agent response
            logger.debug(
                "Room %s: Waiting for agent response (min_offset=%s)...",
                room_id,
                current_offset + 1,
            )

            try:
                has_update = await app.sessions.wait_for_more_events(  # pyrefly: ignore[missing-attribute]
                    session_id=session_id,
                    min_offset=current_offset + 1,
                    kinds=[EventKind.MESSAGE],
                    source=EventSource.AI_AGENT,
                    timeout=Timeout(poll),
                )
                logger.debug(
                    "Room %s: wait_for_more_events returned: %s", room_id, has_update
                )
            except Exception as e:
                logger.error(
                    "Room %s: Error waiting for update: %s",
                    room_id,
                    e,
                    exc_info=True,
                )
                # Check if message was sent via tool before giving up
                if was_message_sent(session_id_str):
                    logger.debug(
                        "Room %s: Message was sent via tool, error is acceptable",
                        room_id,
                    )
                return

            if not has_update:
                # Empty poll window. If a tool already sent the reply we're done;
                # otherwise keep waiting until the budget — don't drop a slow turn.
                if was_message_sent(session_id_str):
                    logger.debug(
                        "Room %s: No new events but message was sent via tool, OK",
                        room_id,
                    )
                    return
                backoff = min(
                    EMPTY_READ_BACKOFF_SECONDS, deadline - time.perf_counter()
                )
                if backoff > 0:
                    await asyncio.sleep(backoff)
                continue

            # Get new events
            try:
                events = await app.sessions.find_events(
                    session_id=session_id,
                    min_offset=current_offset + 1,
                    source=EventSource.AI_AGENT,
                    kinds=[EventKind.MESSAGE],
                    trace_id=None,  # Required by Parlant SDK v3.x
                )
                logger.debug("Room %s: Found %s agent events", room_id, len(events))
            except Exception as e:
                logger.error(
                    "Room %s: Error finding events: %s",
                    room_id,
                    e,
                    exc_info=True,
                )
                return

            if not events:
                # A positive signal with no query-visible event yet is a transient
                # read, not the answer: keep polling until the budget rather than
                # dropping the turn (the same class of bug as a single empty window).
                logger.warning(
                    "Room %s: No events found despite update signal; still waiting",
                    room_id,
                )
                backoff = min(
                    EMPTY_READ_BACKOFF_SECONDS, deadline - time.perf_counter()
                )
                if backoff > 0:
                    await asyncio.sleep(backoff)
                continue

            # Process events and track if we got a non-preamble message
            got_final_message = False

            for event in events:
                logger.debug(
                    "Room %s: Event kind=%s, source=%s, data=%s",
                    room_id,
                    event.kind,
                    event.source,
                    event.data,
                )

                # Update offset for next iteration
                if hasattr(event, "offset") and event.offset > current_offset:
                    current_offset = event.offset

                if (
                    event.kind == EventKind.MESSAGE
                    and event.source == EventSource.AI_AGENT
                ):
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

                    # Check if this is a preamble message
                    is_preamble = PARLANT_PREAMBLE_TAG in tags

                    if is_preamble:
                        logger.debug(
                            "Room %s: Skipping preamble message: %s...",
                            room_id,
                            message_content[:50],
                        )
                        continue

                    # Check if message was already sent via the send_message tool
                    # If so, don't send Parlant's response (would be duplicate/empty)
                    # Also don't mark as final - Parlant may still have more tool calls
                    if was_message_sent(session_id_str):
                        logger.debug(
                            "Room %s: Message already sent via tool, skipping Parlant response: %s...",
                            room_id,
                            message_content[:50],
                        )
                        continue

                    # This is a final message (Parlant generated a response, not via tool)
                    got_final_message = True

                    if message_content:
                        logger.debug(
                            "Room %s: Sending agent response to platform: %s...",
                            room_id,
                            message_content[:100],
                        )
                        try:
                            await tools.send_message(
                                message_content, mentions=[sender_name]
                            )
                            logger.info("Room %s: Message sent successfully", room_id)
                        except Exception as e:
                            logger.error(
                                "Room %s: Error sending message: %s",
                                room_id,
                                e,
                                exc_info=True,
                            )
                    else:
                        logger.warning(
                            "Room %s: Empty message content in event",
                            room_id,
                        )

            # If we got a final (non-preamble) message, we're done
            if got_final_message:
                logger.debug("Room %s: Got final message, processing complete", room_id)
                return

            # Check if message was sent via tool (tool execution may happen without final message)
            if was_message_sent(session_id_str):
                logger.debug(
                    "Room %s: Message sent via tool, no need to wait for final message",
                    room_id,
                )
                return

            # Otherwise, continue waiting for the final message after tool execution
            logger.debug(
                "Room %s: Only got preamble, continuing to wait for final message...",
                room_id,
            )

        # Budget exhausted without a final message. A preamble alone is an
        # acknowledgment ("one moment…"), not an answer — Parlant intermittently
        # stalls the post-preamble generation. We do NOT forward the preamble as the
        # reply: that would make the turn look answered when the agent actually failed
        # the user. Give up honestly; the turn produced no answer.
        if was_message_sent(session_id_str):
            logger.info(
                "Room %s: Response budget elapsed but message was sent via tool, OK",
                room_id,
            )
        else:
            logger.warning(
                "Room %s: Timed out after %ss waiting for agent response",
                room_id,
                self._response_timeout,
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
            logger.exception("Failed to send error event")

    async def cleanup_all(self) -> None:
        """Release all sessions and the owned Parlant server (call on stop)."""
        self._room_sessions.clear()
        self._room_customers.clear()
        await self._release_server()
        self._started = False
        logger.info("Parlant adapter cleanup complete")

    async def _release_server(self) -> None:
        """Tear down the adapter-owned server; a borrowed one is left running."""
        self._app = None
        server_cm, self._server_cm = self._server_cm, None
        if server_cm is None:
            return
        self._server = None
        if self._created_agent:
            self._parlant_agent = None
            self._guidelines_applied_count = 0
        try:
            await server_cm.__aexit__(None, None, None)
        except Exception:
            logger.exception("Parlant server shutdown failed")
