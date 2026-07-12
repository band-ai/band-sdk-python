"""GitHub Copilot SDK adapter for Band.

Bridges Band rooms to the GitHub Copilot SDK (``github-copilot-sdk``),
which manages the Copilot CLI runtime subprocess internally. One adapter
owns one Copilot client; each room gets its own Copilot session so
history, memory, and context stay isolated per room.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import ValidationError

from band.converters.copilot_sdk import (
    SESSION_ID_METADATA_KEY,
    CopilotSDKHistoryConverter,
    CopilotSDKSessionState,
)
from band.core.exceptions import BandConfigError
from band.core.simple_adapter import SimpleAdapter
from band.core.tool_filter import filter_tool_schemas
from band.core.types import Capability, Emit, MessageType, TurnUsage
from band.integrations.copilot_sdk import CopilotSessionManager
from band.integrations.copilot_sdk.prompts import TURN_COMPLETION_GUIDANCE
from band.integrations.copilot_sdk.room_ask_user import (
    ASK_USER_ROOM,
    ROOM_ASK_USER_GUIDANCE,
    delivery_failed_answer,
    question_delivered_answer,
    render_room_question,
    room_inactive_answer,
)
from band.runtime.custom_tools import (
    custom_tools_to_schemas,
    execute_custom_tool,
    find_custom_tool,
    format_validation_error,
)
from band.runtime.prompts import render_system_prompt
from band.runtime.tools import (
    SELF_REPORTING_TOOL_NAMES,
    get_band_tool_category,
    is_room_posting_tool,
)

try:
    from copilot import CopilotClient, PermissionHandler, Tool, ToolResult
    from copilot.generated.session_events import (
        AssistantReasoningData,
        AssistantUsageData,
    )

    _COPILOT_SDK_AVAILABLE = True
except ImportError:
    _COPILOT_SDK_AVAILABLE = False

if TYPE_CHECKING:
    from collections.abc import Callable

    from copilot import (
        CopilotSession,
        ProviderConfig,
        SessionEvent,
        ToolInvocation,
        UserInputHandler,
        UserInputRequest,
        UserInputResponse,
    )

    from band.core.protocols import AgentToolsProtocol, HistoryConverter
    from band.core.types import AdapterFeatures, PlatformMessage
    from band.runtime.custom_tools import CustomToolDef

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CopilotSDKAdapterConfig:
    """Runtime configuration for Copilot SDK adapter sessions.

    Attributes:
        model: Copilot model to use (None = Copilot CLI default).
        custom_section: Extra system-prompt section.
        reasoning_effort: Reasoning effort for reasoning-capable models.
        provider: BYOK ``ProviderConfig`` (e.g. ``ProviderConfig(
            type="openai", base_url=..., api_key=...)``) to run inference
            against your own key instead of the Copilot subscription;
            ``model`` then names the provider's model.
        inject_history_on_resume_failure: Inject text history into a
            fresh session when resuming a persisted session fails.
        session_id_prefix: Prefix for per-room Copilot session ids. None
            (default) derives ``band-{agent-id}-`` from the immutable Band
            agent id, so agents sharing a host or client never resume each
            other's sessions; when set explicitly it must be unique per
            agent.
        base_directory: Copilot state directory (COPILOT_HOME). Set a
            per-agent directory to fully isolate on-disk state between
            agents sharing a host.
        github_token: GitHub token for Copilot auth. Auth resolves
            automatically: the token wins when set, otherwise the locally
            logged-in GitHub user is used — so both fields can usually stay
            at their defaults.
        use_logged_in_user: Force using the logged-in GitHub user; None
            (default) lets the SDK resolve it from ``github_token``.
        turn_timeout_s: Max seconds to wait for a turn to complete.
        ask_user: Routing for Copilot's built-in ``ask_user``
            human-in-the-loop tool; ``None`` (default) keeps the tool
            disabled.

            ``"room"`` routes questions to the people in the Band room:
            the question posts as a room message mentioning whoever
            triggered the turn, the tool call resolves immediately with
            a delivery acknowledgement so the turn ends, and the answer
            arrives as the next room message on the same persisted
            session. This is the only routing that fits both runtimes —
            Band delivers a room's messages strictly one at a time, so
            a turn blocked on a room reply could never receive it, and
            Copilot keeps an unanswered ``ask_user`` pending forever
            (no timeout, no cancellation, not replayed on resume). See
            ``band.integrations.copilot_sdk.room_ask_user``.

            A callable answers on behalf of someone *outside* the room
            (terminal operator, approval service). It is awaited
            mid-turn with ``(UserInputRequest, {"session_id"})`` and
            must return ``{"answer", "wasFreeform"}``; the turn keeps
            counting against ``turn_timeout_s`` while it waits, so
            raise ``turn_timeout_s`` above the handler's own answer
            window or the turn dies before the human can answer. For a
            terminal-backed handler use
            :class:`band.integrations.copilot_sdk.OperatorConsole` — it
            covers the edge cases the SDK leaves to the host (no
            handler timeout, no cancellation on abort, no answer
            validation). Its default answer window fits under this
            default turn timeout; when raising ``answer_timeout_s``,
            raise ``turn_timeout_s`` above it (e.g. 300/600).
    """

    model: str | None = None
    custom_section: str = ""
    reasoning_effort: str | None = None
    provider: ProviderConfig | None = None
    inject_history_on_resume_failure: bool = True
    session_id_prefix: str | None = None
    base_directory: str | None = None
    github_token: str | None = None
    use_logged_in_user: bool | None = None
    turn_timeout_s: float = 120.0
    ask_user: UserInputHandler | Literal["room"] | None = None


@dataclass
class RoomSessionIds:
    """The room's Copilot session id: current vs last persisted to platform."""

    current: str | None = None
    persisted: str | None = None


@dataclass
class TurnState:
    """Mutable per-turn scratch state; turns are serialized per room."""

    # Mention target for anything this turn posts to the room: whoever
    # sent the message that triggered it.
    sender_mention: dict[str, str]
    # True once the turn produced a room message (a Band messaging tool or a
    # room-routed ask_user question) — the final text then must not be
    # auto-sent on top of it.
    replied_in_room: bool = False
    # Reasoning blocks keyed by reasoning_id: the CLI re-emits a block's
    # ``assistant.reasoning`` event several times per turn (same id), so keying
    # by id posts each block once, not 2-3x. Last write wins.
    reasonings: dict[str, str] = field(default_factory=dict)
    # Summed across the turn's per-call assistant.usage events; emitted once.
    usage: TurnUsage = field(default_factory=TurnUsage)


class CopilotSDKAdapter(SimpleAdapter[CopilotSDKSessionState]):
    """Adapter for the GitHub Copilot SDK.

    Maps each Band room to a dedicated Copilot session (stable
    ``session_id`` per room enables resume across restarts). Band platform
    tools and developer custom tools are bridged as native Copilot tools
    whose handlers execute in-process; Copilot built-in tools (shell/file)
    stay disabled so the agent only sees Band's tool surface.

    Example:
        adapter = CopilotSDKAdapter(
            CopilotSDKAdapterConfig(model="gpt-5"),
            # Event reporting is off by default; opt in explicitly.
            features=AdapterFeatures(emit={Emit.EXECUTION, Emit.THOUGHTS}),
        )
        agent = Agent.create(adapter=adapter, agent_id=..., api_key=...)
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.EXECUTION, Emit.THOUGHTS, Emit.USAGE}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        config: CopilotSDKAdapterConfig | None = None,
        *,
        history_converter: HistoryConverter[CopilotSDKSessionState] | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        features: AdapterFeatures | None = None,
        client: Any | None = None,
        client_factory: Callable[[], Any] | None = None,
    ):
        """Initialize the Copilot SDK adapter.

        Args:
            config: Value settings for sessions (model, provider, auth,
                prompts, timeouts) — see :class:`CopilotSDKAdapterConfig`.
            history_converter: Override the default history converter.
            additional_tools: Developer custom tools as (InputModel, handler).
            features: Shared adapter feature settings.
            client: Externally-owned ``CopilotClient`` shared with other
                adapters (one client, many sessions). The adapter borrows it —
                it never calls ``stop()`` on it; the caller owns its lifecycle.
                Give each sharing agent a distinct ``session_id_prefix`` so
                per-room session ids can't collide.
            client_factory: Factory returning a Copilot client the adapter
                owns (created and stopped by the adapter; test seam).
        """
        if not _COPILOT_SDK_AVAILABLE:
            raise ImportError(
                "github-copilot-sdk is required for CopilotSDKAdapter.\n"
                "Install with: pip install 'band-sdk[copilot_sdk]'\n"
                "Requires GitHub Copilot authentication (token or logged-in user)."
            )
        if client is not None and client_factory is not None:
            raise BandConfigError(
                "Pass either client (shared, caller-owned) or client_factory "
                "(adapter-owned), not both."
            )

        super().__init__(
            history_converter=history_converter or CopilotSDKHistoryConverter(),
            features=features,
        )
        self.config = config or CopilotSDKAdapterConfig()
        ask_user = self.config.ask_user
        if (
            ask_user is not None
            and not callable(ask_user)
            and ask_user != ASK_USER_ROOM
        ):
            raise BandConfigError(
                f"ask_user must be {ASK_USER_ROOM!r}, a handler callable, or "
                f"None — got {ask_user!r}."
            )

        self._shared_client = client
        self._client_factory = client_factory
        self._custom_tools: list[CustomToolDef] = list(additional_tools or [])
        self._session_manager: CopilotSessionManager | None = None
        # Refreshed every on_message; tool handlers resolve through this so
        # they never stay bound to a stale tools object from an earlier turn.
        self._room_tools: dict[str, AgentToolsProtocol] = {}
        self._session_ids: dict[str, RoomSessionIds] = {}
        self._turn_state: dict[str, TurnState] = {}
        self._system_prompt: str = ""

    # --- Lifecycle -------------------------------------------------------

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        await super().on_started(agent_name, agent_description)
        # Fixed for the adapter's lifetime — render once, not per session.
        # Adapter-contributed SDK-contract sections (not developer instructions):
        # turn-completion is always required (the CLI runtime's continue-nudge
        # affects every turn); room-mode ask_user adds its own section on top.
        extra_sections = [TURN_COMPLETION_GUIDANCE]
        if self.config.ask_user == ASK_USER_ROOM:
            extra_sections.append(ROOM_ASK_USER_GUIDANCE)
        self._system_prompt = render_system_prompt(
            agent_name=self.agent_name,
            agent_description=self.agent_description,
            custom_section=self.config.custom_section,
            features=self.features,
            extra_sections=tuple(extra_sections),
        )

        if self._shared_client is not None:
            client = self._shared_client
        elif self._client_factory is not None:
            client = self._client_factory()
        else:
            client = CopilotClient(
                base_directory=self.config.base_directory,
                github_token=self.config.github_token,
                use_logged_in_user=self.config.use_logged_in_user,
            )
        self._session_manager = CopilotSessionManager(
            client, owns_client=self._shared_client is None
        )
        # Start eagerly so the runtime download/spawn cost lands at boot
        # (visible in logs) instead of inside the first message's turn.
        await self._session_manager.ensure_started()
        try:
            await self._check_auth(client)
        except BaseException:
            # Failed startup must not leak a running owned client
            # (Agent.stop is never called when start() raises).
            await self._session_manager.stop()
            raise
        logger.info(
            "CopilotSDKAdapter started (agent=%s, model=%s)",
            agent_name,
            self.config.model or "<copilot default>",
        )

    @staticmethod
    async def _check_auth(client: Any) -> None:
        """Fail fast with an actionable message when Copilot auth is missing."""
        get_auth_status = getattr(client, "get_auth_status", None)
        if get_auth_status is None:  # test fakes / exotic clients
            return
        status = await get_auth_status()
        if not getattr(status, "isAuthenticated", True):
            raise BandConfigError(
                "Not authenticated with GitHub Copilot: "
                f"{getattr(status, 'statusMessage', None) or 'no credentials found'}. "
                "Log in with the GitHub CLI (gh auth login) or set a token via "
                "CopilotSDKAdapterConfig(github_token=...)."
            )

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up the room's Copilot session when the agent leaves.

        The client stays alive for the adapter's lifetime (it serves all
        rooms); ``cleanup_all`` stops it at shutdown (unless it was borrowed
        via ``client=`` — then its owner stops it).
        """
        if self._session_manager:
            await self._session_manager.cleanup_session(room_id)
        self._room_tools.pop(room_id, None)
        self._session_ids.pop(room_id, None)
        self._turn_state.pop(room_id, None)

    async def cleanup_all(self) -> None:
        """Stop all sessions and, if adapter-owned, the Copilot client."""
        if self._session_manager:
            await self._session_manager.stop()
        self._room_tools.clear()
        self._session_ids.clear()
        self._turn_state.clear()

    # --- Message handling --------------------------------------------------

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: CopilotSDKSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        if self._session_manager is None:
            raise RuntimeError(
                "CopilotSDKAdapter session manager not initialized — was on_started() called?"
            )

        self._room_tools[room_id] = tools
        if is_session_bootstrap and history.session_id:
            ids = self._session_ids.setdefault(room_id, RoomSessionIds())
            ids.persisted = ids.persisted or history.session_id

        # Same-session calls must not interleave; other rooms run concurrently.
        async with self._session_manager.turn_lock(room_id):
            session, inject_text = await self._obtain_session(
                room_id, history, tools, is_session_bootstrap=is_session_bootstrap
            )
            prompt = self._compose_prompt(
                msg,
                participants_msg,
                contacts_msg,
                room_id=room_id,
                inject_text=inject_text,
            )

            # The session's tool handler records band_send_message calls in
            # this slot; safe because the turn lock serializes turns per room.
            turn = TurnState(
                sender_mention={
                    "id": msg.sender_id,
                    "name": msg.sender_name or msg.sender_type,
                }
            )
            self._turn_state[room_id] = turn
            try:
                final_text = await self._run_turn(session, prompt, turn)
            except Exception as exc:
                logger.exception("Room %s: Copilot turn failed", room_id)
                # Abort any work the runtime is still doing for this turn and
                # drop the session; the next message resumes it fresh by id.
                await self._session_manager.evict_session(room_id)
                await self._report_error(tools, str(exc))
                raise
            finally:
                self._turn_state.pop(room_id, None)
                # No-op unless Emit.USAGE is on / usage is non-empty; best-effort,
                # never raises. In the finally so a turn that errors or times out
                # AFTER a model call still reports the tokens it spent.
                await self.emit_usage(tools, turn.usage)

            await self._emit_thoughts(turn, tools)

            # Session errors raise out of send_and_wait, so a None here
            # with no room output means the model genuinely said nothing.
            if final_text is None and not turn.replied_in_room:
                await self._report_error(tools, "no assistant reply")
                raise RuntimeError("Copilot turn produced no reply")

            # The turn may already have replied into the room; sending its
            # final text too would duplicate the reply.
            if final_text and not turn.replied_in_room:
                await tools.send_message(final_text, mentions=[turn.sender_mention])

            await self._persist_session_id(room_id, tools)

        logger.debug("Room %s: message %s processed", room_id, msg.id)

    # --- Session handling --------------------------------------------------

    def _ids(self, room_id: str) -> RoomSessionIds:
        """Return (creating if needed) the room's session-id record."""
        return self._session_ids.setdefault(room_id, RoomSessionIds())

    def _session_prefix(self) -> str:
        """Return the per-room session-id prefix.

        The default folds in the Band agent id (set by the runtime before
        ``on_started``; immutable, unlike the display name) so two agents
        in the same room never compute the same session id — and a rename
        doesn't orphan persisted sessions.
        """
        if self.config.session_id_prefix is not None:
            return self.config.session_id_prefix
        if agent_id := getattr(self, "_band_agent_id", None):
            return f"band-{agent_id}-"
        # Adapters driven without the Band runtime fall back to the name.
        agent_slug = re.sub(r"[^a-z0-9]+", "-", self.agent_name.lower()).strip("-")
        return f"band-{agent_slug or 'agent'}-"

    async def _obtain_session(
        self,
        room_id: str,
        history: CopilotSDKSessionState,
        tools: AgentToolsProtocol,
        *,
        is_session_bootstrap: bool,
    ) -> tuple[CopilotSession, str | None]:
        """Return the room's session plus history text to inject, if any.

        Resume is attempted when a persisted session id is known; on miss
        the session is created fresh and the converter's text history is
        injected (subject to ``inject_history_on_resume_failure``) so
        context survives a fresh process/host.
        """
        manager = self._session_manager
        assert manager is not None  # guarded by on_message

        session = manager.get_session(room_id)
        if session is not None:
            return session, None

        stored_id = self._ids(room_id).current
        if is_session_bootstrap:
            stored_id = history.session_id or stored_id

        await manager.ensure_started()
        kwargs = self._session_kwargs(
            room_id, self._build_bridged_tools(room_id, tools)
        )
        session = (
            await self._resume_session(room_id, stored_id, kwargs)
            if stored_id
            else None
        )
        resumed = session is not None
        if session is None:
            session = await self._create_session(room_id, kwargs)
        manager.store_session(room_id, session)

        inject = (
            not resumed
            and bool(history.text)
            and (stored_id is None or self.config.inject_history_on_resume_failure)
        )
        return session, history.text if inject else None

    async def _resume_session(
        self, room_id: str, stored_id: str, kwargs: dict[str, Any]
    ) -> CopilotSession | None:
        """Resume a persisted session, or return None when its state is absent."""
        assert self._session_manager is not None
        try:
            session = await self._session_manager.client.resume_session(
                stored_id, **kwargs
            )
        except Exception as exc:
            logger.warning(
                "Room %s: resume failed for session %s: %s — creating fresh",
                room_id,
                stored_id,
                exc,
            )
            return None
        self._ids(room_id).current = stored_id
        logger.info("Room %s: resumed Copilot session %s", room_id, stored_id)
        return session

    async def _create_session(
        self, room_id: str, kwargs: dict[str, Any]
    ) -> CopilotSession:
        """Create a fresh session under the room's deterministic id."""
        assert self._session_manager is not None
        new_id = f"{self._session_prefix()}{room_id}"
        session = await self._session_manager.client.create_session(
            session_id=new_id, **kwargs
        )
        self._ids(room_id).current = new_id
        logger.info("Room %s: created Copilot session %s", room_id, new_id)
        return session

    def _session_kwargs(
        self, room_id: str, bridged_tools: list[Tool]
    ) -> dict[str, Any]:
        """Return the shared kwargs for create_session / resume_session."""
        # Restrict the session to our bridged tools — keeps Copilot's
        # built-in shell/file tools off, which also makes approve_all safe.
        available_tools = [tool.name for tool in bridged_tools]
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "reasoning_effort": self.config.reasoning_effort,
            "provider": self.config.provider,
            "tools": bridged_tools,
            "system_message": {"mode": "replace", "content": self._system_prompt},
            "available_tools": available_tools,
            "on_permission_request": PermissionHandler.approve_all,
        }
        if self.config.ask_user is not None:
            # ask_user is session-isolated (no shell/file/host access), so
            # allowing it does not weaken the approve_all stance above.
            available_tools.append("ask_user")
            kwargs["on_user_input_request"] = (
                self.config.ask_user
                if callable(self.config.ask_user)
                else self._make_room_ask_user_handler(room_id)
            )
        return kwargs

    def _make_room_ask_user_handler(self, room_id: str) -> UserInputHandler:
        """Bind the room id for the session's ask_user → room bridge."""

        async def handle(
            request: UserInputRequest, _context: dict[str, str]
        ) -> UserInputResponse:
            return await self._deliver_question_to_room(room_id, request)

        return handle

    async def _deliver_question_to_room(
        self, room_id: str, request: UserInputRequest
    ) -> UserInputResponse:
        """Post an ask_user question to the room and resolve the tool call.

        Never blocks the turn on the reply: the room's messages are
        processed one at a time, so an answer could not be delivered while
        this turn holds the loop — and Copilot would keep the request
        pending forever (no timeout, not replayed on resume). The question
        becomes the turn's room output and the answer arrives as the next
        message on the same persisted session.
        """
        room_tools = self._room_tools.get(room_id)
        turn = self._turn_state.get(room_id)
        if room_tools is None or turn is None:
            # A late dispatch after the turn/room ended (the SDK never
            # cancels pending asks) must degrade, not crash the RPC.
            return room_inactive_answer()
        rendered = render_room_question(request)
        try:
            await room_tools.send_message(rendered, mentions=[turn.sender_mention])
        except Exception as exc:
            logger.warning(
                "Room %s: ask_user question delivery failed: %s", room_id, exc
            )
            return delivery_failed_answer(exc)
        # The question is this turn's reply; suppress the final-text
        # fallback so a "waiting for your answer" wrap-up can't shadow it.
        self._mark_replied_in_room(room_id, turn)
        # The ack echoes the rendered form so the model knows exactly what
        # the user sees — e.g. that a bare "2" means numbered choice 2.
        return question_delivered_answer(rendered)

    def _mark_replied_in_room(self, room_id: str, turn: TurnState) -> None:
        """Record that ``turn`` produced a room message.

        Guarded by identity: an operation orphaned by a turn timeout (the
        SDK never cancels in-flight dispatches) must not mark a LATER turn
        as having replied.
        """
        if self._turn_state.get(room_id) is turn:
            turn.replied_in_room = True

    # --- Tool bridging -------------------------------------------------------

    def _build_bridged_tools(
        self, room_id: str, tools: AgentToolsProtocol
    ) -> list[Tool]:
        """Bridge Band platform tools + developer custom tools as Copilot tools."""
        schemas = tools.get_openai_tool_schemas(
            include_memory=Capability.MEMORY in self.features.capabilities,
            include_contacts=Capability.CONTACTS in self.features.capabilities,
        )
        schemas = filter_tool_schemas(
            schemas,
            self.features,
            get_name=lambda s: s.get("function", {}).get("name", ""),
            get_category=lambda s: get_band_tool_category(
                s.get("function", {}).get("name", "")
            ),
        )
        merged = schemas + custom_tools_to_schemas(self._custom_tools, "openai")

        handler = self._make_tool_handler(room_id)
        bridged: list[Tool] = []
        seen: set[str] = set()
        for schema in merged:
            function = schema.get("function") or {}
            name = function.get("name")
            if name and name not in seen:
                seen.add(name)
                bridged.append(
                    Tool(
                        name=name,
                        description=function.get("description") or "",
                        parameters=function.get("parameters")
                        or {"type": "object", "properties": {}},
                        handler=handler,
                        # Band/custom tools execute in-process against the
                        # platform API — Copilot's permission flow adds nothing.
                        skip_permission=True,
                    )
                )
        return bridged

    def _make_tool_handler(self, room_id: str) -> Callable[[ToolInvocation], Any]:
        """Build the per-room tool handler Copilot invokes for bridged tools.

        A closure only to bind ``room_id``; the logic lives in
        :meth:`_execute_bridged_tool`.
        """

        async def handle(invocation: ToolInvocation) -> ToolResult:
            return await self._execute_bridged_tool(room_id, invocation)

        return handle

    async def _execute_bridged_tool(
        self, room_id: str, invocation: ToolInvocation
    ) -> ToolResult:
        """Execute one bridged tool call, reporting it as platform events."""
        tool_name = invocation.tool_name
        arguments = (
            invocation.arguments if isinstance(invocation.arguments, dict) else {}
        )
        # Resolve at call time: sessions outlive any single message, and a
        # fresh AgentToolsProtocol arrives with every on_message. The turn
        # is captured here (before any await) so a call orphaned by a turn
        # timeout can never mark a LATER turn as having replied.
        turn = self._turn_state.get(room_id)
        room_tools = self._room_tools.get(room_id)
        if room_tools is None:
            return ToolResult(
                text_result_for_llm=f"Room {room_id} is no longer active",
                result_type="failure",
                error="room inactive",
            )

        should_report = (
            Emit.EXECUTION in self.features.emit
            and tool_name not in SELF_REPORTING_TOOL_NAMES
        )
        if should_report:
            await self._report_tool_call(room_tools, invocation, arguments)

        try:
            custom_tool = find_custom_tool(self._custom_tools, tool_name)
            if custom_tool:
                result = await execute_custom_tool(custom_tool, arguments)
            else:
                # Structured variant: a base tool (e.g. band_send_message) can fail
                # without raising (bad args, API error) — that surfaces as ok=False,
                # not an exception. Treat it as a failure so the turn is NOT marked
                # replied and the final-text fallback still fires (avoids a silent
                # turn). Mirrors the Slack adapter.
                outcome = await room_tools.execute_tool_call_structured(
                    tool_name, arguments
                )
                if not outcome.ok:
                    return await self._fail_tool_call(
                        room_tools,
                        invocation,
                        outcome.error_message or str(outcome.value),
                        report=should_report,
                    )
                result = outcome.value
        except ValidationError as exc:
            logger.error("Validation error for tool %s: %s", tool_name, exc)
            error_text = (
                f"Invalid arguments for {tool_name}: {format_validation_error(exc)}"
            )
            return await self._fail_tool_call(
                room_tools, invocation, error_text, report=should_report
            )
        except Exception as exc:
            logger.exception("Tool execution failed for %s", tool_name)
            return await self._fail_tool_call(
                room_tools, invocation, f"Error: {exc}", report=should_report
            )

        text_result = (
            result if isinstance(result, str) else json.dumps(result, default=str)
        )
        if is_room_posting_tool(tool_name) and turn is not None:
            self._mark_replied_in_room(room_id, turn)
        if should_report:
            await self._report_tool_result(room_tools, invocation, text_result)
        return ToolResult(text_result_for_llm=text_result)

    async def _fail_tool_call(
        self,
        room_tools: AgentToolsProtocol,
        invocation: ToolInvocation,
        error_text: str,
        *,
        report: bool,
    ) -> ToolResult:
        """Report a failed tool call and wrap it as an LLM-readable failure."""
        if report:
            await self._report_tool_result(room_tools, invocation, error_text)
        return ToolResult(
            text_result_for_llm=error_text,
            result_type="failure",
            error=error_text,
        )

    # Payload keys (name/args/output/tool_call_id) are what the history
    # converters parse back out of tool events — keep them in sync.

    async def _report_tool_call(
        self,
        room_tools: AgentToolsProtocol,
        invocation: ToolInvocation,
        arguments: dict[str, Any],
    ) -> None:
        await self._send_event_safe(
            room_tools,
            json.dumps(
                {
                    "name": invocation.tool_name,
                    "args": arguments,
                    "tool_call_id": invocation.tool_call_id,
                }
            ),
            MessageType.TOOL_CALL,
        )

    async def _report_tool_result(
        self,
        room_tools: AgentToolsProtocol,
        invocation: ToolInvocation,
        output: str,
    ) -> None:
        await self._send_event_safe(
            room_tools,
            json.dumps(
                {
                    "name": invocation.tool_name,
                    "output": output,
                    "tool_call_id": invocation.tool_call_id,
                }
            ),
            MessageType.TOOL_RESULT,
        )

    # --- Turn execution ------------------------------------------------------

    def _compose_prompt(
        self,
        msg: PlatformMessage,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        room_id: str,
        inject_text: str | None,
    ) -> str:
        room_context = f"[room_id: {room_id}]"
        parts: list[str] = []
        if inject_text:
            parts.append(f"[Previous conversation context:]\n{inject_text}")
        if participants_msg:
            parts.append(f"{room_context}[System]: {participants_msg}")
        if contacts_msg:
            parts.append(f"{room_context}[System]: {contacts_msg}")
        parts.append(f"{room_context}{msg.format_for_llm()}")
        return "\n\n".join(parts)

    async def _run_turn(
        self, session: CopilotSession, prompt: str, turn: TurnState
    ) -> str | None:
        """Send one prompt, wait for idle, and return the final assistant text.

        Session errors need no collecting here: ``send_and_wait`` raises on
        any session error event, which surfaces through on_message's
        turn-failure path.
        """

        def collect(event: SessionEvent) -> None:
            data = event.data
            if isinstance(data, AssistantReasoningData) and data.content:
                turn.reasonings[data.reasoning_id] = data.content
            elif isinstance(data, AssistantUsageData):
                turn.usage += self._usage_from_event(data)  # sum per-call usage

        unsubscribe = session.on(collect)
        try:
            final_event = await session.send_and_wait(
                prompt, timeout=self.config.turn_timeout_s
            )
        finally:
            unsubscribe()

        content = getattr(final_event.data, "content", None) if final_event else None
        # An empty final message means no reply — same as no final event.
        return content.strip() or None if isinstance(content, str) else None

    @staticmethod
    def _usage_from_event(data: AssistantUsageData) -> TurnUsage:
        """Map one assistant.usage event onto TurnUsage.

        assistant.usage is per-API-call, so a turn's events are summed by the
        caller. reasoning_tokens is NOT folded into output_tokens — the
        providers Copilot relays (OpenAI/Anthropic) already count reasoning
        inside their output/completion tokens, so folding would double-count
        (unlike codex/opencode, whose SDK totals report reasoning disjointly).
        cache_* may arrive as 0 — a Copilot CLI runtime limitation, not a
        schema bug — mapped anyway: harmless at 0, correct once the runtime
        populates them.
        """
        return TurnUsage.from_object(
            data,
            input="input_tokens",
            output="output_tokens",
            cache_read="cache_read_tokens",
            cache_write="cache_write_tokens",
        )

    # --- Event emission ------------------------------------------------------

    async def _emit_thoughts(self, turn: TurnState, tools: AgentToolsProtocol) -> None:
        if Emit.THOUGHTS in self.features.emit:
            for reasoning in turn.reasonings.values():
                await self._send_event_safe(tools, reasoning, MessageType.THOUGHT)

    async def _persist_session_id(
        self, room_id: str, tools: AgentToolsProtocol
    ) -> None:
        """Persist the room's session id as a task event (enables rehydration).

        Always emitted (once per id) — resume across restarts depends on it,
        so it is bookkeeping, not opt-in observability.
        """
        ids = self._ids(room_id)
        if ids.current and ids.persisted != ids.current:
            sent = await self._send_event_safe(
                tools,
                "Copilot SDK session",
                MessageType.TASK,
                metadata={SESSION_ID_METADATA_KEY: ids.current},
            )
            if sent:
                # Only mark persisted on success so a transient send failure
                # is retried next turn instead of silently losing resume.
                ids.persisted = ids.current

    async def _send_event_safe(
        self,
        tools: AgentToolsProtocol,
        content: str,
        message_type: MessageType,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Send a platform event, downgrading failures to a warning.

        Returns True when the event was accepted by the platform.
        """
        try:
            await tools.send_event(
                content=content, message_type=message_type, metadata=metadata
            )
        except Exception as exc:
            logger.warning("Failed to send %s event: %s", message_type, exc)
            return False
        return True

    async def _report_error(self, tools: AgentToolsProtocol, error: str) -> None:
        await self._send_event_safe(tools, f"Error: {error}", MessageType.ERROR)
