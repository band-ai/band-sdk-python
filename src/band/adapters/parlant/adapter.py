"""
Parlant adapter using the official Parlant SDK directly.

This adapter integrates the Parlant framework (https://github.com/emcie-co/parlant)
with the Band platform.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn

from band_sdk_core import AgentFailure
from typing_extensions import Unpack

from band.adapters.parlant.config import ConfigureCallback, ParlantAdapterConfig
from band.adapters.parlant.guidelines import GuidelineLedger, GuidelineSpec
from band.adapters.parlant.history import inject_history
from band.adapters.parlant.lifecycle import ServerLifecycle
from band.adapters.parlant.messages import compose_user_message, post_user_message
from band.adapters.parlant.responses import relay_agent_response
from band.adapters.parlant.rooms import RoomSessions
from band.converters.parlant import ParlantHistoryConverter, ParlantMessages
from band.core.delivery import DeliveryFailedError, reraise_delivery_cause
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE, AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import Capability, Emit, FeatureKwargs, PlatformMessage
from band.integrations.parlant.customschema import check_parlant_custom_tools
from band.integrations.parlant.sessiontools import bound_session_tools
from band.integrations.parlant.tools import create_parlant_tools
from band.runtime.custom_tools import CustomToolDef, get_custom_tool_name

if TYPE_CHECKING:
    import parlant.sdk as p
    from parlant.core.application import Application
    from parlant.core.sessions import SessionId

logger = logging.getLogger(__name__)

PROVIDER = "parlant"
NOT_INITIALIZED_ERROR = "Parlant Application not initialized"
# Display names used when the platform supplies none.
FALLBACK_SENDER_NAME = "User"
FALLBACK_AGENT_NAME = "Assistant"
UNREACHABLE_CUSTOM_TOOLS_WARNING = (
    "Parlant can never call custom tools %s: no guideline keeps the default "
    "tools. Declare one with add_guideline(), or attach them from adapter.tools "
    "in configure=."
)


class ParlantAdapter(SimpleAdapter[ParlantMessages]):
    """
    Parlant adapter using the official Parlant SDK directly.

    The adapter owns the Parlant server lifecycle: it reserves free ports, boots
    ``p.Server`` when the Band agent starts, and tears it down when the agent
    stops. Guidelines are declared up front with :meth:`add_guideline` and created
    on the live agent at startup, with the adapter's tools (Band platform tools
    plus ``additional_tools``) attached by default. Parlant calls a tool only
    through a matched guideline or journey.

    Example:
        import parlant.sdk as p
        from band import Agent
        from band.adapters import ParlantAdapter, ParlantAdapterConfig

        adapter = ParlantAdapter(
            ParlantAdapterConfig(name="Assistant", description="A helpful assistant"),
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
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )

    @property
    def judges_turns(self) -> bool:
        """Parlant's own engine owns its replies, so its turns are not judged."""
        return False

    def __init__(
        self,
        config: ParlantAdapterConfig | None = None,
        *,
        history_converter: ParlantHistoryConverter | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        nlp_service: Any | None = None,
        server_options: dict[str, Any] | None = None,
        server: p.Server | None = None,
        parlant_agent: p.Agent | None = None,
        configure: ConfigureCallback | None = None,
        **features: Unpack[FeatureKwargs],
    ):
        """
        Initialize the Parlant SDK adapter.

        Args:
            config: Adapter settings; defaults to ``ParlantAdapterConfig()``.
            history_converter: Custom history converter (optional)
            additional_tools: Custom tools as ``(InputModel, handler)`` pairs,
                offered with the Band platform tools by every guideline that
                keeps the default tools. Fields must be scalars, enums, dates
                or lists of those; Parlant has no object parameter type.
                Names are server-wide, so on a shared ``server=`` they must
                not clash with another adapter's tools.
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
                Cannot be combined with ``config.system_prompt`` /
                ``config.custom_section``.
            configure: Async callback ``(server, parlant_agent)`` run at startup
                after guidelines are applied, for full native Parlant API access.
        """
        super().__init__(
            history_converter=history_converter or ParlantHistoryConverter(),
            **features,
        )
        self.config = config or ParlantAdapterConfig()
        self._lifecycle = ServerLifecycle(
            server=server,
            agent=parlant_agent,
            nlp_service=nlp_service,
            server_options=server_options or {},
        )

        if parlant_agent is not None and (
            self.config.system_prompt is not None
            or self.config.custom_section is not None
        ):
            raise ValueError(
                "system_prompt/custom_section shape the adapter-created agent's "
                "description; they cannot be applied to a caller-provided "
                "parlant_agent="
            )

        self._custom_tools = additional_tools or []
        check_parlant_custom_tools(self._custom_tools)
        self._configure = configure
        self._guidelines = GuidelineLedger()
        # The adapter's tools as Parlant ToolEntry objects (built at start)
        self._tools: list[Any] = []
        self._rooms: RoomSessions | None = None
        self._started = False

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
        are forwarded to it verbatim. ``tools`` defaults to the adapter's
        tools (Band platform tools plus ``additional_tools``); pass an explicit
        sequence (including ``[]``) to override.

        For live guideline management (return values, dependencies, or a
        per-guideline tool selection from ``adapter.tools``), use the
        ``configure=`` callback instead.
        """
        if self._started:
            raise RuntimeError(
                "add_guideline must be called before the agent starts; use the "
                "configure= callback or adapter.parlant_agent.create_guideline() "
                "for a running agent"
            )
        self._guidelines.declare(GuidelineSpec(condition, action, tools, kwargs))

    @property
    def server(self) -> p.Server:
        """The running Parlant server (available once the Band agent starts)."""
        if self._lifecycle.server is None:
            raise RuntimeError(
                "Parlant server not running yet; it starts with the agent"
            )
        return self._lifecycle.server

    @property
    def parlant_agent(self) -> p.Agent:
        """The Parlant agent (available once the Band agent starts)."""
        if self._lifecycle.agent is None:
            raise RuntimeError(
                "Parlant agent not created yet; it starts with the agent"
            )
        return self._lifecycle.agent

    @property
    def tools(self) -> list[Any]:
        """The adapter's tools (Band platform tools plus ``additional_tools``)
        as Parlant ToolEntry objects, built at startup."""
        return list(self._tools)

    def _agent_instructions(self, agent_description: str) -> str:
        """Behavioral instructions for the adapter-created Parlant agent."""
        if self.config.system_prompt:
            return self.config.system_prompt
        description = self.config.description or agent_description
        if self.config.custom_section:
            description = f"{description}\n\n{self.config.custom_section}"
        return description

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Boot the Parlant server (unless borrowed) and configure the agent."""
        await super().on_started(agent_name, agent_description)

        async def prepare(server: p.Server) -> tuple[p.Agent, Application]:
            return await self._prepare_server(
                server, agent_name=agent_name, agent_description=agent_description
            )

        try:
            app = await self._lifecycle.start(prepare)
        except BaseException:
            self._forget_guidelines_if_agent_gone()
            raise

        self._rooms = RoomSessions(
            server=self.server, app=app, agent_id=self.parlant_agent.id
        )
        self._started = True
        logger.info(
            "Parlant SDK adapter started for agent: %s (parlant_agent_id=%s)",
            agent_name,
            self.parlant_agent.id,
        )

    async def _prepare_server(
        self, server: p.Server, *, agent_name: str, agent_description: str
    ) -> tuple[p.Agent, Application]:
        """Declare everything Parlant must process before its setup phase."""
        agent = self._lifecycle.agent
        if agent is None:
            agent = await server.create_agent(
                name=self.config.name or agent_name,
                description=self._agent_instructions(agent_description),
            )

        self._tools = create_parlant_tools(
            self.features, custom_tools=self._custom_tools
        )
        await self._guidelines.apply_pending(agent, default_tools=self._tools)
        self._warn_if_custom_tools_unreachable()

        if self._configure is not None:
            await self._configure(server, agent)

        from parlant.core.application import Application  # noqa: PLC0415

        return agent, server.container[Application]

    def _warn_if_custom_tools_unreachable(self) -> None:
        """Parlant only calls a tool from a guideline that offers it."""
        if (
            not self._custom_tools
            or self._guidelines.keeps_default_tools
            or self._configure is not None
        ):
            return
        logger.warning(
            UNREACHABLE_CUSTOM_TOOLS_WARNING,
            sorted(get_custom_tool_name(model) for model, _ in self._custom_tools),
        )

    def _forget_guidelines_if_agent_gone(self) -> None:
        """The applied-guideline count belongs to the agent it was applied to."""
        if self._lifecycle.agent is None:
            self._guidelines.forget_applied()

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
        """Post the turn into the room's Parlant session and relay the response."""
        logger.debug("Handling message %s in room %s", msg.id, room_id)
        rooms = self._rooms
        if rooms is None:
            await self._fail_uninitialized(tools)

        sender_name = msg.sender_name or msg.sender_id or FALLBACK_SENDER_NAME
        session_id = await self._session_for(
            rooms, room_id=room_id, sender_name=sender_name, tools=tools
        )
        with bound_session_tools(session_id=str(session_id), tools=tools):
            if is_session_bootstrap and history:
                injected = await inject_history(
                    app=rooms.app,
                    session_id=session_id,
                    history=history,
                    agent_name=self.agent_name or FALLBACK_AGENT_NAME,
                )
                logger.info(
                    "Room %s: Injected %s messages from history", room_id, injected
                )
            user_message = compose_user_message(
                msg=msg, participants_msg=participants_msg, contacts_msg=contacts_msg
            )
            try:
                await self._converse(
                    app=rooms.app,
                    session_id=session_id,
                    user_message=user_message,
                    tools=tools,
                    sender_name=sender_name,
                )
            except DeliveryFailedError as e:
                reraise_delivery_cause(e)
            except Exception:
                logger.exception("Error processing message")
                await tools.send_failure(
                    AgentFailure(PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
                )
                raise

        logger.debug("Message %s processed successfully", msg.id)

    async def _fail_uninitialized(self, tools: AgentToolsProtocol) -> NoReturn:
        logger.error(NOT_INITIALIZED_ERROR)
        await tools.send_failure(AgentFailure(PROVIDER, NOT_INITIALIZED_ERROR))
        raise RuntimeError(NOT_INITIALIZED_ERROR)

    async def _session_for(
        self,
        rooms: RoomSessions,
        *,
        room_id: str,
        sender_name: str,
        tools: AgentToolsProtocol,
    ) -> SessionId:
        try:
            return await rooms.session_for(room_id, customer_name=sender_name)
        except Exception as e:
            logger.error("Failed to get/create session for room %s: %s", room_id, e)
            await tools.send_failure(
                AgentFailure(PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
            )
            raise

    async def _converse(
        self,
        *,
        app: Application,
        session_id: SessionId,
        user_message: str,
        tools: AgentToolsProtocol,
        sender_name: str,
    ) -> None:
        """Post *user_message* as the customer, then relay the engine's reply."""
        offset = await post_user_message(
            app=app, session_id=session_id, message=user_message
        )
        await relay_agent_response(
            app=app,
            session_id=session_id,
            min_offset=offset,
            tools=tools,
            sender_name=sender_name,
            timeout=self.config.response_timeout,
            poll=self.config.response_poll,
        )

    async def on_cleanup(self, room_id: str) -> None:
        """Forget the room's Parlant session when the agent leaves it."""
        if self._rooms is not None:
            self._rooms.forget(room_id)
        logger.debug("Room %s: Cleaned up Parlant session", room_id)

    async def cleanup_all(self) -> None:
        """Release all sessions and the owned Parlant server (call on stop)."""
        self._rooms = None
        await self._lifecycle.release()
        self._forget_guidelines_if_agent_gone()
        self._started = False
        logger.info("Parlant adapter cleanup complete")
