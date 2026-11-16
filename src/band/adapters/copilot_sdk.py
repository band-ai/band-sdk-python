"""GitHub Copilot SDK adapter for Band.

Bridges Band rooms to the GitHub Copilot SDK (``github-copilot-sdk``),
which manages the Copilot CLI runtime subprocess internally. One adapter
owns one Copilot client; each room gets its own Copilot session so
history, memory, and context stay isolated per room.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal
from uuid import uuid4

from band_sdk_core import AgentFailure
from pydantic import Field, PositiveFloat, ValidationError

from band.converters.copilot_sdk import (
    COPILOT_RESUME_RECORD,
    CopilotSDKHistoryConverter,
    CopilotSDKSessionState,
)
from band.core.adapterconfig import SHARED_TRANSITION_TIMEOUT_S, BaseAdapterConfig
from band.core.content import has_visible_content
from band.core.delivery import (
    DeliveryFailedError,
    automatic_mentions,
    deliver_reply,
    finish_automatic_reply,
    reraise_delivery_cause,
)
from band.core.exceptions import (
    HOST_CONTROL_ERRORS,
    BandConfigError,
    CleanupIncompleteError,
)
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE, send_event_safe
from band.core.retention.cleanup import await_owned_result, spawn_owned
from band.core.simple_adapter import BatchInputAdapter
from band.core.tool_filter import filter_tool_schemas
from band.core.turn_lifecycle import BackendTurns
from band.core.types import (
    Capability,
    Emit,
    MessageType,
    ToolEventKey,
    TurnUsage,
    distinct_sender_ids,
    ids_of,
)
from band.integrations.copilot_sdk import CopilotSessionManager
from band.integrations.copilot_sdk.prompts import TURN_COMPLETION_GUIDANCE
from band.integrations.copilot_sdk.room_ask_user import (
    ASK_USER_ROOM,
    ROOM_ASK_USER_GUIDANCE,
    delivery_failed_answer,
    no_recipients_answer,
    question_delivered_answer,
    render_room_question,
    room_inactive_answer,
)
from band.runtime.activity import retain_request_permission
from band.runtime.custom_tools import (
    custom_tools_to_schemas,
    find_custom_tool,
    format_validation_error,
    run_custom_tool,
)
from band.runtime.formatters import format_current_inputs, messages_before
from band.runtime.prompts import render_system_prompt
from band.runtime.sessionresume import SessionSeed
from band.runtime.tools import (
    get_band_tool_category,
    image_block_placeholder,
    is_image_passthrough_result,
    redact_tool_call_args,
)

try:
    from copilot import (
        CopilotClient,
        PermissionHandler,
        Tool,
        ToolBinaryResult,
        ToolResult,
    )
    from copilot.generated.session_events import (
        AbortData,
        AssistantReasoningData,
        AssistantTurnRetryData,
        AssistantUsageData,
        ModelCallFailureData,
        SessionIdleData,
        SessionWarningData,
        ToolExecutionCompleteData,
    )

    from band.integrations.copilot_sdk.activity import SessionActivity

    _COPILOT_SDK_AVAILABLE = True
except ImportError:
    _COPILOT_SDK_AVAILABLE = False

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator, Sequence

    from copilot import (
        CopilotSession,
        ProviderConfig,
        SessionEvent,
        ToolInvocation,
        UserInputHandler,
        UserInputRequest,
        UserInputResponse,
    )
    from typing_extensions import Unpack

    from band.core.protocols import AgentToolsProtocol, HistoryConverter
    from band.core.types import FeatureKwargs, PlatformMessage
    from band.runtime.custom_tools import CustomToolDef

logger = logging.getLogger(__name__)

_PROVIDER = "copilot_sdk"

# Frames the room transcript replayed into a fresh session's seeding prompt.
HISTORY_HEADING = "[Previous conversation context:]"


class CopilotSDKAdapterConfig(BaseAdapterConfig):
    """Runtime configuration for Copilot SDK adapter sessions.

    Attributes:
        model: Copilot model to use (None = Copilot CLI default). With the
            adapter's BYOK ``provider=``, names the provider's model.
        custom_section: Extra system-prompt section.
        reasoning_effort: Reasoning effort for reasoning-capable models.
        inject_history_on_resume_failure: Retained for configuration compatibility.
            A resume that cannot be disposed latches the room unavailable
            without creating a replacement; a disposed resume clears the
            recorded identity so the next turn may create a fresh session.
        session_id_prefix: Prefix for per-room Copilot session ids, which
            names an agent's sessions on disk. None (default) derives
            ``band-{agent-id}-`` from the immutable Band agent id. Each
            fresh session id also carries a random suffix, so ids never
            collide.
        base_directory: Copilot state directory (COPILOT_HOME). Set a
            per-agent directory to fully isolate on-disk state between
            agents sharing a host.
        github_token: GitHub token for Copilot auth. Auth resolves
            automatically: the token wins when set, otherwise the locally
            logged-in GitHub user is used. Not required when the adapter's
            ``provider=`` configures BYOK inference.
        use_logged_in_user: ``True`` forces the logged-in GitHub user;
            ``False`` opts out of GitHub identity entirely (the CLI runs
            with ``--no-auto-login``), which is the BYOK path — pair it
            with ``provider=`` or the runtime has no credentials at all;
            ``None`` (default) lets the SDK resolve it from
            ``github_token``.
        turn_timeout_s: Max seconds to wait for a turn to complete.
        transition_timeout_s: Max seconds for one setup or cleanup transition
            (auth, create/resume, abort join). Expiry abandons the caller wait
            only; it does not prove the Copilot session settled.
    """

    model: str | None = None
    custom_section: str = ""
    reasoning_effort: str | None = None
    inject_history_on_resume_failure: bool = True
    session_id_prefix: str | None = None
    base_directory: str | None = None
    github_token: str | None = Field(default=None, repr=False)
    use_logged_in_user: bool | None = None
    turn_timeout_s: PositiveFloat = 120.0
    transition_timeout_s: PositiveFloat = SHARED_TRANSITION_TIMEOUT_S


@dataclass
class RoomSession:
    """The room's live Copilot session and the session ids it resumes from.

    ``resumable`` moves to the live session only once its seed completes, so a
    session that never received its seed is never resumed.
    """

    live: str | None = None
    seed: SessionSeed = field(default_factory=SessionSeed)
    resumable: str | None = None
    recorded: str | None = None
    # True once bootstrap has considered history's room_to_session entry, so a
    # disposed failed resume is not re-adopted from the same history payload.
    history_applied: bool = False
    activity: SessionActivity | None = None
    # The turn whose idle observer and callback count are still live.
    turn: SessionTurn | None = None

    @property
    def unseeded(self) -> str | None:
        """The live session's id while its seeding prompt has not completed."""
        return self.live if self.seed.replay_due else None

    def confirm_seed(self) -> None:
        """Record that the live session's seeding prompt completed."""
        self.seed.confirm()
        self.resumable = self.live


@dataclass
class TurnState:
    """Mutable per-turn scratch state; turns are serialized per room."""

    # Mentions for anything this turn posts to the room on its own: each
    # distinct sender of the originals it answers, captured at admission.
    audience: tuple[str, ...]
    # Reasoning blocks keyed by reasoning_id: the CLI re-emits a block's
    # ``assistant.reasoning`` event several times per turn (same id), so keying
    # by id posts each block once, not 2-3x. Last write wins.
    reasonings: dict[str, str] = field(default_factory=dict)
    # Summed across the turn's per-call assistant.usage events; emitted once.
    usage: TurnUsage = field(default_factory=TurnUsage)
    # Non-fatal trouble the turn hit, logged when it ends without a reply.
    incidents: list[str] = field(default_factory=list)


@dataclass(eq=False)
class SessionTurn:
    """The session a stop aborts, the room it seeds, and how far its prompt got.

    Identifies the turn to ``BackendTurns``. A stopped turn reopens its room
    only once the session reports a top-level idle and every SDK callback the
    turn started has returned.
    """

    room_id: str
    session: CopilotSession
    room: RoomSession
    # Set when the prompt is sent, cleared once it returns; a prompt that is
    # cancelled or fails leaves it set.
    prompting: bool = False
    inject_text: str | None = None
    failed: bool = False
    cleanup: asyncio.Task[None] | None = None
    # Set by the session's own idle (never a subagent's) after this turn began.
    idled: asyncio.Event = field(default_factory=asyncio.Event)
    # Bridged-tool and ask_user callbacks the SDK started and has not finished.
    callbacks: int = 0
    unsubscribe: Callable[[], None] | None = None

    def stop_observing(self) -> None:
        """Drop the idle observer; ``idled`` keeps what it already saw."""
        if self.unsubscribe is not None:
            self.unsubscribe()
            self.unsubscribe = None
        if self.room.turn is self:
            self.room.turn = None


def _turn_incident(data: object) -> str | None:
    """A one-line account of a session event that can explain a silent turn."""
    match data:
        case ModelCallFailureData():
            kind = data.error_type or (
                data.failure_kind.value if data.failure_kind else "unknown"
            )
            status = "" if data.status_code is None else f" status={data.status_code}"
            return (
                f"model call failed: {kind}{status} {data.error_message or ''}".rstrip()
            )
        case AssistantTurnRetryData():
            return f"turn retried: {data.reason or 'no reason given'}"
        case SessionWarningData():
            return f"warning {data.warning_type}: {data.message}"
        case AbortData():
            return f"aborted: {data.reason.value}"
        case ToolExecutionCompleteData(success=False):
            error = data.error.message if data.error else "no error detail"
            return f"tool {data.tool_call_id} failed: {error}"
        case _:
            return None


class CopilotSDKAdapter(BatchInputAdapter[CopilotSDKSessionState]):
    """Adapter for the GitHub Copilot SDK.

    Maps each Band room to a dedicated Copilot session (the room's
    ``COPILOT_RESUME_RECORD`` enables resume across restarts). Band platform
    tools and developer custom tools are bridged as native Copilot tools
    whose handlers execute in-process; Copilot built-in tools (shell/file)
    stay disabled so the agent only sees Band's tool surface.

    Example:
        adapter = CopilotSDKAdapter(
            CopilotSDKAdapterConfig(model="gpt-6-luna"),
            # Narrowing is opt-in; the default is everything supported.
            emit=Emit.TOOL_CALLS | Emit.THOUGHTS,
        )
        agent = Agent.create(adapter=adapter, agent_id=..., api_key=...)
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.TOOL_CALLS, Emit.THOUGHTS, Emit.USAGE}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )

    def __init__(
        self,
        config: CopilotSDKAdapterConfig | None = None,
        *,
        history_converter: HistoryConverter[CopilotSDKSessionState] | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        client: Any | None = None,
        client_factory: Callable[[], Any] | None = None,
        provider: ProviderConfig | None = None,
        ask_user: UserInputHandler | Literal["room"] | None = None,
        **features: Unpack[FeatureKwargs],
    ):
        """Initialize the Copilot SDK adapter.

        Args:
            config: Session settings (model, auth, prompts, timeouts) — see
                :class:`CopilotSDKAdapterConfig`.
            history_converter: Override the default history converter.
            additional_tools: Developer custom tools as (InputModel, handler).
            client: Externally-owned ``CopilotClient`` shared with other
                adapters (one client, many sessions). The adapter borrows it —
                it never calls ``stop()`` on it; the caller owns its lifecycle.
            client_factory: Factory returning a Copilot client the adapter
                owns (created and stopped by the adapter; test seam).
            provider: BYOK ``ProviderConfig`` (e.g. ``ProviderConfig(
                type="openai", base_url=..., api_key=...)``) to run inference
                against your own key instead of the Copilot subscription;
                ``config.model`` then names the provider's model. It may
                carry a ``bearer_token_provider`` callback, so it is not
                config data.
            ask_user: Routing for Copilot's built-in ``ask_user``
                human-in-the-loop tool; ``None`` (default) keeps the tool
                disabled.

                ``"room"`` routes questions to the people in the Band room:
                the question posts as a room message mentioning the
                turn's senders, the tool call resolves immediately with
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
                counting against ``config.turn_timeout_s`` while it waits,
                so raise ``turn_timeout_s`` above the handler's own answer
                window or the turn dies before the human can answer. For a
                terminal-backed handler use
                :class:`band.integrations.copilot_sdk.OperatorConsole` — it
                covers the edge cases the SDK leaves to the host (no
                handler timeout, no cancellation on abort, no answer
                validation). Its default answer window fits under the
                default turn timeout; when raising ``answer_timeout_s``,
                raise ``turn_timeout_s`` above it (e.g. 300/600).
        """
        if not _COPILOT_SDK_AVAILABLE:
            raise ImportError(
                "github-copilot-sdk is required for CopilotSDKAdapter.\n"
                "Install with: pip install 'band-sdk[copilot_sdk]'\n"
                "Requires GitHub Copilot authentication (token or logged-in user)."
            )
        if (
            ask_user is not None
            and not callable(ask_user)
            and ask_user != ASK_USER_ROOM
        ):
            raise BandConfigError(
                f"ask_user must be {ASK_USER_ROOM!r}, a handler callable, or "
                f"None — got {ask_user!r}."
            )
        if client is not None and client_factory is not None:
            raise BandConfigError(
                "Pass either client (shared, caller-owned) or client_factory "
                "(adapter-owned), not both."
            )

        super().__init__(
            history_converter=history_converter or CopilotSDKHistoryConverter(),
            **features,
        )
        self.config = config or CopilotSDKAdapterConfig()
        self._provider = provider
        self._ask_user = ask_user
        self._shared_client = client
        self._client_factory = client_factory
        self._custom_tools: list[CustomToolDef] = list(additional_tools or [])
        self._session_manager: CopilotSessionManager | None = None
        # Rebound when each turn starts; tool handlers resolve through this so
        # they never stay bound to a stale tools object from an earlier turn.
        self._room_tools: dict[str, AgentToolsProtocol] = {}
        self._sessions: dict[str, RoomSession] = {}
        self._turn_state: dict[str, TurnState] = {}
        self._backend_turns: BackendTurns[SessionTurn] = BackendTurns(
            self._abort_turn, provider=_PROVIDER, requires_confirmation=True
        )
        self._system_prompt: str = ""

    # --- Lifecycle -------------------------------------------------------

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        await super().on_started(agent_name, agent_description)
        # Fixed for the adapter's lifetime — render once, not per session.
        # Adapter-contributed SDK-contract sections (not developer instructions):
        # turn-completion is always required (the CLI runtime's continue-nudge
        # affects every turn); room-mode ask_user adds its own section on top.
        extra_sections = [TURN_COMPLETION_GUIDANCE]
        if self._ask_user == ASK_USER_ROOM:
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
        try:
            await await_owned_result(
                self._start_client(client),
                timeout=self.config.transition_timeout_s,
                label="Copilot client start",
            )
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

    async def _start_client(self, client: Any) -> None:
        """Start the Copilot client and confirm auth when hosted inference needs it."""
        assert self._session_manager is not None
        await self._session_manager.ensure_started()
        await self._check_auth(client)

    async def _check_auth(self, client: Any) -> None:
        """Require GitHub auth only when inference uses the Copilot service."""
        # A singular provider replaces Copilot-hosted inference. Current Copilot
        # SDKs intentionally allow that BYOK path with no GitHub identity.
        if self._provider is not None:
            return
        get_auth_status = getattr(client, "get_auth_status", None)
        if get_auth_status is None:  # test fakes / exotic clients
            return
        status = await get_auth_status()
        if not getattr(status, "isAuthenticated", True):
            raise BandConfigError(
                "Not authenticated with GitHub Copilot: "
                f"{getattr(status, 'statusMessage', None) or 'no credentials found'}. "
                "Log in with the GitHub CLI (gh auth login) or set a token via "
                "CopilotSDKAdapterConfig(github_token=...), or pass provider=... "
                "for BYOK inference."
            )

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up the room's Copilot session when the agent leaves.

        The client stays alive for the adapter's lifetime (it serves all
        rooms); ``cleanup_all`` stops it at shutdown (unless it was borrowed
        via ``client=`` — then its owner stops it).
        """
        await self.backend_turns.settled(room_id)
        self._require_session_settled(room_id)
        self._room_tools.pop(room_id, None)
        if self._session_manager:
            await self._session_manager.cleanup_session(room_id)
        if (
            room := self._sessions.get(room_id)
        ) is not None and room.activity is not None:
            await room.activity.close()
        self._sessions.pop(room_id, None)
        self._turn_state.pop(room_id, None)

    def _require_session_settled(self, room_id: str) -> None:
        """Keep callback ownership until the session supplies settlement evidence."""
        if self.backend_turns.blocker(
            room_id
        ) is not None or self.backend_turns.unavailable(room_id):
            raise CleanupIncompleteError("Copilot session settlement is unconfirmed")

    async def cleanup_all(self) -> None:
        """Stop all sessions and, if adapter-owned, the Copilot client."""
        await self.backend_turns.drain()
        for room_id in self._sessions:
            self._require_session_settled(room_id)
        if self._session_manager:
            await self._session_manager.stop()
        for room in self._sessions.values():
            if room.activity is not None:
                await room.activity.close()
        self._room_tools.clear()
        self._sessions.clear()
        self._turn_state.clear()

    # --- Message handling --------------------------------------------------

    @property
    def backend_turns(self) -> BackendTurns[SessionTurn]:
        return self._backend_turns

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
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

        # Same-session calls must not interleave; other rooms run concurrently.
        async with self._session_manager.turn_lock(room_id):
            # Refuse while a prior stop is unconfirmed or the room is unavailable,
            # instead of parking forever under the turn lock on prior.ready.
            await self.backend_turns.require_admission(room_id, tools)
            # A cancelled turn's stop aborts the runtime's work; a seeded
            # session stays valid for the next message, an unseeded one is
            # discarded so the next message starts over and replays.
            async with self.backend_turns.running(room_id):
                # Bound once the previous turn's abort has settled and this
                # message holds the room, so neither a queued message nor a
                # late call from the stopped turn reaches this turn's tools.
                self._room_tools[room_id] = tools
                prepare = asyncio.ensure_future(
                    self._prepare_turn(
                        room_id,
                        history,
                        tools,
                        messages,
                        is_session_bootstrap=is_session_bootstrap,
                    )
                )
                try:
                    live = await self.backend_turns.start_within(
                        room_id,
                        prepare,
                        timeout=self.config.transition_timeout_s,
                        label=f"Copilot session setup for room {room_id}",
                    )
                    assert live is not None
                    session = live.session
                except Exception:
                    logger.exception("Room %s: Copilot session setup failed", room_id)
                    await tools.send_failure(
                        AgentFailure(_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
                    )
                    raise
                if (
                    live.room.activity is None
                    or live.room.activity.session is not session
                ):
                    if live.room.activity is not None:
                        await live.room.activity.close()
                    live.room.activity = SessionActivity(session)
                activity = live.room.activity
                activity.bind(tools)
                prompt = self._compose_prompt(
                    messages,
                    participants_msg,
                    contacts_msg,
                    inject_text=live.inject_text,
                )

                # The session's ask_user handler reads this slot; safe because the
                # turn lock serializes turns per room.
                turn = TurnState(audience=distinct_sender_ids(messages))
                self._turn_state[room_id] = turn
                # Registered with no await before the prompt, and kept past a
                # cancelled or failed prompt until the idle that confirms the stop.
                self._observe_idle(live)
                try:
                    final_text = await self._run_turn(live, prompt, turn)
                except Exception:
                    logger.exception("Room %s: Copilot turn failed", room_id)
                    live.failed = True
                    await self.backend_turns.stop(room_id)
                    await tools.send_failure(
                        AgentFailure(_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
                    )
                    raise
                else:
                    live.stop_observing()
                    live.room.confirm_seed()
                finally:
                    activity.retire(tools)
                    self._turn_state.pop(room_id, None)
                    # No-op unless Emit.USAGE is on / usage is non-empty; best-effort,
                    # never raises. In the finally so a turn that errors or times out
                    # AFTER a model call still reports the tokens it spent.
                    await self.emit_usage(tools, turn.usage)

            await self._emit_thoughts(turn, tools)

            try:
                if not tools.turn.replied and (
                    has_visible_content(final_text or "") or not tools.turn.complete
                ):
                    mentions = await automatic_mentions(tools, turn.audience)
                    if self._room_tools.get(room_id) is not tools:
                        raise asyncio.CancelledError
                    await finish_automatic_reply(tools, final_text, mentions)
            except DeliveryFailedError as e:
                reraise_delivery_cause(e)
            if not tools.turn.complete:
                # The runtime reports the missing reply; incidents carry session
                # text, so they stay in this log line and out of that report.
                logger.warning(
                    "Room %s: Copilot turn ended without a reply (incidents: %s)",
                    room_id,
                    "; ".join(turn.incidents) or "none reported",
                )

            await self._record_session(room_id, tools)

        logger.debug("Room %s: messages %s processed", room_id, ids_of(messages))

    # --- Session handling --------------------------------------------------

    def _room(self, room_id: str) -> RoomSession:
        """Return (creating if needed) the room's session record."""
        return self._sessions.setdefault(room_id, RoomSession())

    async def _drop_session(self, room_id: str, room: RoomSession) -> None:
        """Abort and drop a failed turn's session, discarding it if never seeded.

        An unseeded session was created by this process and is never resumed,
        so its disk state is only clutter; a seeded one is resumed next turn.
        """
        assert self._session_manager is not None
        if (unseeded := room.unseeded) is not None:
            await self._session_manager.discard_session(room_id, unseeded)
        else:
            await self._session_manager.evict_session(room_id)
        if room.activity is not None:
            await room.activity.close()
            room.activity = None

    def _session_prefix(self) -> str:
        """Return the per-room session-id prefix.

        The default folds in the Band agent id (set by the runtime before
        ``on_started``; immutable, unlike the display name), so an agent's
        sessions are recognizable on disk.
        """
        if self.config.session_id_prefix is not None:
            return self.config.session_id_prefix
        if self.platform is not None:
            return f"band-{self.platform.agent_id}-"
        # Adapters driven without the Band runtime fall back to the name.
        agent_slug = re.sub(r"[^a-z0-9]+", "-", self.agent_name.lower()).strip("-")
        return f"band-{agent_slug or 'agent'}-"

    async def _prepare_turn(
        self,
        room_id: str,
        history: CopilotSDKSessionState,
        tools: AgentToolsProtocol,
        messages: Sequence[PlatformMessage],
        *,
        is_session_bootstrap: bool,
    ) -> SessionTurn:
        """Keep setup and its cleanup owned until a concrete session is known."""
        room = self._room(room_id)
        session, inject_text = await self._obtain_session(
            room_id,
            history,
            tools,
            messages,
            is_session_bootstrap=is_session_bootstrap,
        )
        return SessionTurn(room_id, session, room, inject_text=inject_text)

    async def _obtain_session(
        self,
        room_id: str,
        history: CopilotSDKSessionState,
        tools: AgentToolsProtocol,
        messages: Sequence[PlatformMessage],
        *,
        is_session_bootstrap: bool,
    ) -> tuple[CopilotSession, str | None]:
        """Return the room's session plus history text to inject, if any.

        Without a live session, the room's resumable session is resumed, or
        a fresh one created when no persisted identity exists. Until its seed
        completes, a fresh session is owed the room's transcript.
        """
        manager = self._session_manager
        assert manager is not None  # guarded by on_message

        room = self._room(room_id)
        session = manager.get_session(room_id)
        if session is None:
            # A seeded session this process knows is never older than the record.
            if (
                is_session_bootstrap
                and room.resumable is None
                and not room.history_applied
            ):
                room.resumable = room.recorded = history.room_to_session.get(room_id)
                room.history_applied = True
            await manager.ensure_started()
            kwargs = self._session_kwargs(
                room_id, self._build_bridged_tools(room_id, tools)
            )
            session = (
                await self._resume_session(room_id, room.resumable, kwargs)
                if room.resumable
                else None
            )
            if session is None:
                session = await self._create_session(room_id, kwargs)
            manager.store_session(room_id, session)

        if not room.seed.replay_due:
            return session, None
        text = (
            history.text
            if is_session_bootstrap
            else await self._fetch_transcript(tools, room_id, messages)
        )
        return session, text or None

    async def _fetch_transcript(
        self,
        tools: AgentToolsProtocol,
        room_id: str,
        messages: Sequence[PlatformMessage],
    ) -> str:
        """Fetch the room transcript for a session seeded off-bootstrap.

        The runtime hands history over only on bootstrap. The transcript
        stops before the turn's earliest input, which this turn delivers.
        """
        assert self.history_converter is not None  # set in __init__
        try:
            context = await tools.fetch_room_context(room_id=room_id)
            raw = messages_before(context.get("data") or [], messages)
        except Exception:
            logger.warning(
                "Room %s: could not fetch history to seed the new Copilot session",
                room_id,
                exc_info=True,
            )
            return ""
        return self.history_converter.convert(raw).text

    async def _resume_session(
        self, room_id: str, stored_id: str, kwargs: dict[str, Any]
    ) -> CopilotSession:
        """Resume only when the provider confirms all session setup completed."""
        assert self._session_manager is not None
        room = self._room(room_id)
        try:
            session = await self._session_manager.client.resume_session(
                stored_id, **kwargs
            )
        except Exception:
            # Resume may have partially applied options; dispose before latching.
            try:
                await self._session_manager.client.delete_session(stored_id)
            except Exception:
                self.backend_turns.abandon_start(room_id)
                raise
            room.resumable = None
            if room.recorded == stored_id:
                room.recorded = None
            raise
        room.live, room.seed = stored_id, SessionSeed(restored=True)
        logger.info("Room %s: resumed Copilot session %s", room_id, stored_id)
        return session

    async def _create_session(
        self, room_id: str, kwargs: dict[str, Any]
    ) -> CopilotSession:
        """Create a fresh, unseeded session under a new id.

        Every fresh session gets its own id, so creating one never overwrites
        the native state of a session the room may still resume.
        """
        assert self._session_manager is not None
        room = self._room(room_id)
        new_id = f"{self._session_prefix()}{room_id}-{uuid4().hex}"
        try:
            session = await self._session_manager.client.create_session(
                session_id=new_id, **kwargs
            )
        except BaseException:
            # The runtime may have created it before failing or being stopped.
            try:
                await self._session_manager.client.delete_session(new_id)
            except BaseException:
                self.backend_turns.abandon_start(room_id)
                raise
            # No prompt was sent; joined deletion retires this failed setup.
            self.backend_turns.confirm_stopped(room_id, None)
            raise
        room.live, room.seed = new_id, SessionSeed()
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
            "provider": self._provider,
            "tools": bridged_tools,
            "system_message": {"mode": "replace", "content": self._system_prompt},
            "available_tools": available_tools,
            "on_permission_request": PermissionHandler.approve_all,
        }
        if self._ask_user is not None:
            # ask_user is session-isolated (no shell/file/host access), so
            # allowing it does not weaken the approve_all stance above.
            available_tools.append("ask_user")
            kwargs["on_user_input_request"] = (
                self._manual_user_input_handler(room_id, self._ask_user)
                if callable(self._ask_user)
                else self._make_room_ask_user_handler(room_id)
            )
        return kwargs

    def _manual_user_input_handler(
        self, room_id: str, handler: UserInputHandler
    ) -> UserInputHandler:
        def handle(request: UserInputRequest, context: dict[str, str]) -> Any:
            tools = self._room_tools.get(room_id)
            if tools is not None:
                retain_request_permission(tools)
            result = handler(request, context)
            if not inspect.isawaitable(result):
                return result
            return self._await_counted(room_id, result)

        return handle

    async def _await_counted(self, room_id: str, pending: Awaitable[Any]) -> Any:
        """Await a developer handler's answer as one counted SDK callback."""
        with self._callback(room_id):
            return await pending

    def _make_room_ask_user_handler(self, room_id: str) -> UserInputHandler:
        """Bind the room id for the session's ask_user → room bridge."""

        async def handle(
            request: UserInputRequest, _context: dict[str, str]
        ) -> UserInputResponse:
            with self._callback(room_id):
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
        # The question is the turn's reply, so a "waiting for your answer"
        # wrap-up is not relayed on top of it.
        try:
            mentions = await automatic_mentions(room_tools, turn.audience)
            if self._turn_state.get(room_id) is not turn:
                return room_inactive_answer()
            if not mentions:
                return no_recipients_answer()
            await deliver_reply(room_tools, rendered, mentions=mentions)
        except DeliveryFailedError as exc:
            logger.warning(
                "Room %s: ask_user question delivery failed: %s", room_id, exc.cause
            )
            return delivery_failed_answer(exc.cause)
        # The ack echoes the rendered form so the model knows exactly what
        # the user sees — e.g. that a bare "2" means numbered choice 2.
        return question_delivered_answer(rendered)

    # --- Tool bridging -------------------------------------------------------

    def _build_bridged_tools(
        self, room_id: str, tools: AgentToolsProtocol
    ) -> list[Tool]:
        """Bridge Band platform tools + developer custom tools as Copilot tools."""
        schemas = tools.get_openai_tool_schemas(
            capabilities=self.features.capabilities,
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
            with self._callback(room_id):
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
        # fresh AgentToolsProtocol arrives with every turn. Captured
        # before any await, so a call orphaned by a turn timeout records its
        # effect on its own turn's ledger, never a LATER turn's.
        room_tools = self._room_tools.get(room_id)
        if room_tools is None:
            return ToolResult(
                text_result_for_llm=f"Room {room_id} is no longer active",
                result_type="failure",
                error="room inactive",
            )

        should_report = Emit.TOOL_CALLS in self.features.emit
        if should_report:
            await self._report_tool_call(room_tools, invocation, arguments)

        try:
            custom_tool = find_custom_tool(self._custom_tools, tool_name)
            # Structured: either kind of tool can fail without raising (bad args,
            # API error, a failure value) via ok=False, which must reach the model
            # as a failed call.
            if custom_tool:
                outcome = await run_custom_tool(
                    custom_tool, arguments, tools=room_tools
                )
            else:
                outcome = await room_tools.execute_tool_call_structured(
                    tool_name, arguments
                )
        except HOST_CONTROL_ERRORS:
            raise
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

        if not outcome.ok:
            return await self._fail_tool_call(
                room_tools,
                invocation,
                outcome.error_message or str(outcome.value),
                report=should_report,
            )
        result = outcome.value

        binary_results: list[ToolBinaryResult] | None = None
        if is_image_passthrough_result(tool_name, result):
            # Malformed native image content must reach the model as a failure.
            try:
                binary_results = [
                    ToolBinaryResult(
                        data=block["data"], mime_type=block["mimeType"], type="image"
                    )
                    for block in result["content"]
                ]
            except (KeyError, TypeError) as exc:
                logger.exception("Malformed image content block for %s", tool_name)
                return await self._fail_tool_call(
                    room_tools, invocation, f"Error: {exc}", report=should_report
                )
            text_result = image_block_placeholder(len(binary_results))
        else:
            text_result = (
                result if isinstance(result, str) else json.dumps(result, default=str)
            )
        if should_report:
            await self._report_tool_result(room_tools, invocation, text_result)
        return ToolResult(
            text_result_for_llm=text_result, binary_results_for_llm=binary_results
        )

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
        await send_event_safe(
            room_tools,
            json.dumps(
                {
                    ToolEventKey.NAME: invocation.tool_name,
                    ToolEventKey.ARGS: redact_tool_call_args(
                        invocation.tool_name, arguments
                    ),
                    ToolEventKey.TOOL_CALL_ID: invocation.tool_call_id,
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
        await send_event_safe(
            room_tools,
            json.dumps(
                {
                    ToolEventKey.NAME: invocation.tool_name,
                    ToolEventKey.OUTPUT: output,
                    ToolEventKey.TOOL_CALL_ID: invocation.tool_call_id,
                }
            ),
            MessageType.TOOL_RESULT,
        )

    # --- Turn execution ------------------------------------------------------

    def _compose_prompt(
        self,
        messages: Sequence[PlatformMessage],
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        inject_text: str | None,
    ) -> str:
        parts: list[str] = []
        if inject_text:
            parts.append(f"{HISTORY_HEADING}\n{inject_text}")
        if participants_msg:
            parts.append(f"[System]: {participants_msg}")
        if contacts_msg:
            parts.append(f"[System]: {contacts_msg}")
        parts.append(format_current_inputs(messages))
        return "\n\n".join(parts)

    async def _abort_turn(self, live: SessionTurn) -> None:
        """Request stop; failed-session cleanup stays owned for late confirmation.

        Do not await retirement here: a stop budget must not cancel the delete
        that still owns settlement evidence after abort returns.
        """
        assert self._session_manager is not None
        await self._abort_prompt(live)
        self._settle(live)

    async def _abort_prompt(self, live: SessionTurn) -> None:
        """Abort the prompt and, while one is running, wait for the idle that ends it.

        The CLI emits that idle just after acknowledging the abort, and
        ``send_and_wait`` ends on any idle: a next prompt sent before it
        arrives returns on it with no reply. The turn's own observer, live
        since before the prompt, also catches an idle that arrives ahead of the
        acknowledgement. This wait is bounded by the backend stop's limit, but
        the observer is not: a late idle still confirms the stop.
        """
        await live.session.abort()
        if live.prompting:
            await live.idled.wait()
        else:
            # No prompt was sent, so no idle is owed.
            live.idled.set()

    def _observe_idle(self, live: SessionTurn) -> None:
        """Watch the turn's session for the top-level idle that ends its prompt."""

        def on_event(event: SessionEvent) -> None:
            # A subagent's idle says nothing about the turn's own work.
            if isinstance(event.data, SessionIdleData) and event.agent_id is None:
                live.idled.set()
                self._settle(live)

        live.room.turn = live
        live.unsubscribe = live.session.on(on_event)

    def _settle(self, live: SessionTurn) -> None:
        """Reopen the room once its stopped turn is idle and its callbacks returned.

        Only a stop already requested for this turn can be confirmed: a normal
        turn's own idle must not release its registration.
        """
        if not live.idled.is_set() or live.callbacks:
            return
        if self.backend_turns.unconfirmed_handle(live.room_id) is not live:
            return
        if live.failed or live.room.unseeded is not None:
            if live.cleanup is None:
                live.cleanup = spawn_owned(
                    self._retire_session(live),
                    label=f"Copilot session cleanup for room {live.room_id}",
                )
            return
        live.stop_observing()
        self.backend_turns.confirm_stopped(live.room_id, live)

    async def _retire_session(self, live: SessionTurn) -> None:
        """Join session cleanup before a successor can bind the room's activity."""
        await self._drop_session(live.room_id, live.room)
        live.stop_observing()
        self.backend_turns.confirm_stopped(live.room_id, live)

    @contextmanager
    def _callback(self, room_id: str) -> Iterator[None]:
        """Count one SDK callback against the room's turn until it returns."""
        room = self._sessions.get(room_id)
        live = room.turn if room is not None else None
        if live is not None:
            live.callbacks += 1
        try:
            yield
        finally:
            if live is not None:
                live.callbacks -= 1
                self._settle(live)

    async def _run_turn(
        self, live: SessionTurn, prompt: str, turn: TurnState
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
            elif incident := _turn_incident(data):
                turn.incidents.append(incident)

        unsubscribe = live.session.on(collect)
        live.prompting = True
        try:
            final_event = await live.session.send_and_wait(
                prompt, timeout=self.config.turn_timeout_s
            )
        finally:
            unsubscribe()
        live.prompting = False

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
                await send_event_safe(tools, reasoning, MessageType.THOUGHT)

    async def _record_session(self, room_id: str, tools: AgentToolsProtocol) -> None:
        """Post the room's resume record once per resumable session id.

        A failed post leaves the id unrecorded, so the next turn retries it.
        """
        room = self._room(room_id)
        if room.resumable == room.recorded:
            return
        if await COPILOT_RESUME_RECORD.post(
            tools, room_id=room_id, seed=room.seed, session=room.resumable
        ):
            room.recorded = room.resumable
