"""ACP adapter that bridges Band rooms to a remote ACP runtime."""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import Any, ClassVar, Generic, TypeAlias

from acp import spawn_agent_process
from acp.exceptions import RequestError
from acp.schema import (
    AcpMcpServer,
    ClientCapabilities,
    HttpHeader,
    HttpMcpServer,
    McpServerStdio,
    NewSessionResponse,
    PermissionOption,
    SetSessionConfigOptionResponse,
    SseMcpServer,
)
from band_sdk_core import AgentFailure
from pydantic import (
    Field,
    PositiveFloat,
    field_validator,
    model_validator,
)
from typing_extensions import TypeVar, Unpack

from band.converters.acp_client import (
    ACP_CLIENT_RESUME_RECORD,
    ACPClientHistoryConverter,
)
from band.converters.helpers import (
    HISTORY_REPLAY_HEADER,
    build_replay_messages,
    new_message_marker,
)
from band.core.adapterconfig import BaseAdapterConfig
from band.core.delivery import DeliveryFailedError, reraise_delivery_cause
from band.core.exceptions import (
    BandConfigError,
    InputNotAccepted,
)
from band.core.model_catalog import ModelSelection
from band.core.protocols import (
    FAILURE_CODE_TIMEOUT,
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    AgentToolsProtocol,
)
from band.core.retention import (
    ProviderActivity,
    ReleaseCause,
    ReleaseOutcome,
    RoomRetention,
    RoomRetentionConfig,
    require_disabled_retention,
)
from band.core.retention.cleanup import spawn_owned
from band.core.simple_adapter import BatchInputAdapter
from band.core.tool_filter import filter_tool_schemas
from band.core.turn_lifecycle import (
    TURN_SETTLE_GRACE_S,
    BackendTurns,
    TurnSettleTimeout,
)
from band.core.types import (
    AdapterFeatures,
    Capability,
    Emit,
    FeatureKwargs,
    PlatformMessage,
)
from band.integrations.acp.client_profiles import ACPClientProfile
from band.integrations.acp.client_runtime import ACPRuntime
from band.integrations.acp.client_types import (
    ACPClientSessionState,
    ACPRoom,
    BandACPClient,
    RoomSession,
    is_session_busy,
)
from band.integrations.acp.collecting import ACPCollectingClient
from band.integrations.acp.model_selection import (
    ACPModelOptions,
    apply_model_selection,
    locate_model_options,
)
from band.integrations.acp.permissions import (
    ElicitationHandler,
    ElicitationNarrator,
    PermissionHandler,
    PermissionNarrator,
    allow_permission,
    cancel_permission,
    permission_option_ids,
    select_allow_option_id,
)
from band.integrations.acp.room_emitter import RoomTurnEmitter
from band.integrations.acp.session_config import (
    CONFIG_FAILURE_PREFIX,
    RESOLVER_CONFIG_OPTION_ID,
    ACPConfigError,
    ACPConfigRequest,
    ACPConfigUnreachableError,
    SessionConfigOption,
    SessionConfigResolver,
    SessionConfigSetter,
    apply_session_config_selections,
)
from band.integrations.acp.sessioninit import SessionSetupObserver
from band.integrations.acp.types import (
    ACP_FAILURE_PROVIDER,
    CANCELLED_STOP,
    REFUSAL_STOP,
    ACPToolActivity,
    ACPToolCall,
)
from band.integrations.mcp import (
    BandMCPBackend,
    BandMCPBackendSettings,
    BandMCPTransport,
    SharedBandMCPBackend,
)
from band.runtime.activity import UNKNOWN_ACTIVITY_TOOL_NAME
from band.runtime.custom_tools import (
    CustomToolDef,
    get_custom_tool_name,
)
from band.runtime.formatters import (
    SENDER_METADATA_INSTRUCTION,
    format_current_inputs,
    messages_before,
)
from band.runtime.prompts import (
    COMMUNICATION_INSTRUCTIONS,
    TURN_COMPLETION_GUIDANCE,
    render_system_prompt,
    system_updates,
)
from band.runtime.sessionresume import SessionSeed
from band.runtime.tools import (
    BAND_MCP_SERVER_NAME,
    CHAT_ID_FIELD_NAME,
    LEGACY_SEND_MESSAGE_TOOL,
    ToolDefinition,
    canonicalize_mcp_tool_name,
    get_band_tool_category,
    iter_tool_definitions,
)
from band.workspaces import (
    RoomWorkspaces,
    WorkspaceResolver,
    workspace_resolver_for,
)

logger = logging.getLogger(__name__)

PermissionOptionValue: TypeAlias = PermissionOption | Mapping[str, object]
PermissionResolver: TypeAlias = Callable[
    ["ACPPermissionRequest"], Awaitable[str | None]
]


@dataclass(frozen=True)
class ACPPermissionRequest:
    """The permission choices advertised for one ACP tool call."""

    room_id: str
    session_id: str
    tool_call: ACPToolCall
    options: tuple[PermissionOptionValue, ...]


@dataclass(frozen=True)
class SessionMcpServers:
    """The MCP servers a session is created or loaded with."""

    servers: list[object]
    band_url: str | None


@dataclass(frozen=True)
class ACPTurn:
    """What stopping one room's in-flight prompt needs."""

    runtime: ACPRuntime
    session_id: str


class ACPTurnTimeoutError(TimeoutError):
    """The adapter deadline expired before the ACP prompt completed."""


LocalMcpServerConfig = HttpMcpServer | SseMcpServer
# What ACP's session/new takes; YAML/JSON entries validate into these.
SessionMcpServer = HttpMcpServer | SseMcpServer | AcpMcpServer | McpServerStdio
ResolvedMcpServer: TypeAlias = HttpMcpServer | SseMcpServer | McpServerStdio
McpServerResolver: TypeAlias = Callable[[str], Awaitable[Sequence[ResolvedMcpServer]]]


class ACPMcpServerResolutionError(ValueError):
    """The room's resolved MCP servers cannot be used by its ACP agent."""


SESSION_CLOSE_TIMEOUT_SECONDS = 5.0
DEFAULT_TURN_TIMEOUT_SECONDS = 300.0
# One acquisition launches the agent and runs initialize/authenticate, and a
# wake also reloads its session.
ACP_TRANSITION_TIMEOUT_S = 30.0
# How a turn reports a room process that did not start or stop within
# ``retention.transition_timeout_s``.
PROCESS_NOT_READY_ERROR = "ACP agent process did not become ready in time"
# Opens the Band context sent with the first prompt of an ACP session.
SYSTEM_CONTEXT_HEADER = "[System Context]"


class SessionCloseReason(StrEnum):
    """Why an ACP session is closed before a room is done with it (for logs)."""

    UNCONFIGURED = "unconfigured"
    STALE_BAND_MCP_URL = "stale Band MCP URL"
    UNSEEDED = "seeding prompt did not complete"


def _setup_interrupted(room: ACPRoom) -> bool:
    task = asyncio.current_task()
    return task is not None and (task.cancelling() > 0 or task is room.abandoned_setup)


# The transport seam: a callable matching ACPRuntime's spawn_process contract —
# ``(client, *command, env=..., transport_kwargs=...) -> async CM yielding (conn, _)``.
# The adapter validates the transport boundary, while ACPRuntime retains this seam
# for lower-level runtime tests and direct runtime consumers.
SpawnProcess = Callable[..., object]


def _resolve_launcher(command: list[str]) -> list[str]:
    """Resolve the launcher to its full path so the subprocess spawns on Windows.

    An npm-installed launcher like ``npx`` is ``npx.cmd`` on Windows, and
    ``create_subprocess_exec`` does not apply PATHEXT to a bare name — so it fails
    with ``FileNotFoundError``. ``shutil.which`` finds the ``.cmd`` shim (and the
    plain binary on POSIX). A name that can't be resolved is left as-is, so a
    genuinely missing binary still fails loudly at spawn.
    """
    if not command:
        return command
    resolved = shutil.which(command[0])
    return [resolved, *command[1:]] if resolved else list(command)


def _config_setter(runtime: ACPRuntime) -> SessionConfigSetter:
    """``session/set_config_option`` on ``runtime``, in the applier's shape."""

    async def set_option(
        session_id: str, option_id: str, value: str
    ) -> SetSessionConfigOptionResponse | None:
        return await runtime.set_config_option(
            session_id=session_id, config_id=option_id, value=value
        )

    return set_option


def _to_agent_failure(exc: Exception) -> AgentFailure:
    """Parse a turn-ending exception into the shared provider-failure shape.

    ``RequestError`` is raised for a JSON-RPC error the remote agent
    returned; its numeric ``code``/``data`` carry more than the generic
    message alone.
    """
    if isinstance(exc, RequestError):
        return AgentFailure(ACP_FAILURE_PROVIDER, str(exc), str(exc.code), exc.data)
    return AgentFailure(ACP_FAILURE_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)


# A TCP endpoint is one shared process, so it cannot serve one room each.
_TCP_SETTINGS = ("host", "port")
_TCP_TRANSPORT_REJECTED = "TCP ACP transport cannot guarantee room process isolation"


class ACPClientAdapterConfig(BaseAdapterConfig):
    """Settings for bridging Band rooms to an ACP agent over stdio.

    Each room gets its own agent subprocess launched in the room's workspace.

    Attributes:
        command: The agent's launch command; a single string is one argv
            element.
        retention: Residency settings; ``transition_timeout_s``
            (``ACP_TRANSITION_TIMEOUT_S`` by default) bounds each start, so a
            slower one fails its turn and its room recovers.
        env: Extra environment for the agent subprocess.
        cwd: Root under which each room gets its own workspace directory;
            exclusive with the adapter's ``workspace_for_room``.
        mcp_servers: MCP servers passed to each new ACP session.
        inject_band_tools: Serve the Band tools to each session over a
            loopback MCP server.
        auth_method: ACP auth method to ``authenticate`` with after
            ``initialize``; ``None`` skips authentication.
        custom_section: Extra instructions added to the session's system
            context.
        use_unstable_protocol: Speak ACP's unstable protocol methods.
        turn_timeout_s: Seconds a prompt may run before the turn is cancelled
            and a ``timeout`` failure is posted to the room.
        model: Model selected from each session's advertised catalog; a value
            the catalog does not offer fails the turn.
        reasoning_effort: Reasoning effort selected the same way.
    """

    retention_unavailable_reason: ClassVar[str] = (
        "ACP end_turn and loadSession do not establish whole-session inactivity "
        "or non-cancelling shutdown"
    )

    command: tuple[str, ...]
    retention: RoomRetentionConfig = Field(
        default_factory=lambda: RoomRetentionConfig(
            transition_timeout_s=ACP_TRANSITION_TIMEOUT_S
        )
    )
    env: dict[str, str] | None = None
    cwd: str | None = None
    mcp_servers: tuple[SessionMcpServer, ...] = ()
    inject_band_tools: bool = True
    auth_method: str | None = None
    custom_section: str = ""
    use_unstable_protocol: bool = False
    turn_timeout_s: PositiveFloat = DEFAULT_TURN_TIMEOUT_SECONDS
    turn_settle_timeout_s: TurnSettleTimeout = TURN_SETTLE_GRACE_S
    model: str | None = None
    reasoning_effort: str | None = None

    @field_validator("retention")
    @classmethod
    def _require_safe_retention(
        cls, retention: RoomRetentionConfig
    ) -> RoomRetentionConfig:
        return require_disabled_retention(
            retention,
            reason=f"Idle retention is unavailable: {cls.retention_unavailable_reason}.",
        )

    @model_validator(mode="before")
    @classmethod
    def _reject_tcp_transport(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and any(name in data for name in _TCP_SETTINGS):
            raise ValueError(_TCP_TRANSPORT_REJECTED)
        return data

    @field_validator("command", mode="before")
    @classmethod
    def _split_command(cls, command: Any) -> Any:
        if isinstance(command, str):
            return (command,) if command else ()
        return command

    @field_validator("command")
    @classmethod
    def _require_command(cls, command: tuple[str, ...]) -> tuple[str, ...]:
        if not command:
            raise ValueError("ACP stdio transport requires a command")
        return command


# Lets each backend subclass type ``self.config`` as its own config class.
ACPClientAdapterConfigT = TypeVar(
    "ACPClientAdapterConfigT",
    bound=ACPClientAdapterConfig,
    default=ACPClientAdapterConfig,
)


class ACPClientAdapter(
    BatchInputAdapter[ACPClientSessionState], Generic[ACPClientAdapterConfigT]
):
    """Adapter that forwards Band messages to a remote ACP agent.

    The adapter owns Band bridge concerns such as room-to-session mapping,
    session rehydration, system-context bootstrapping, Band MCP injection,
    and emitting replies back to the platform. ACP subprocess lifecycle,
    prompt delivery, and session-update buffering live in ``ACPRuntime``.

    Sessions follow the resume rule in ``band.runtime.sessionresume``; how
    a seeding prompt settles is in ``_settle_seed``.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.TOOL_CALLS, Emit.THOUGHTS, Emit.TASK_EVENTS}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )

    def __init__(
        self,
        config: ACPClientAdapterConfigT,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        workspace_for_room: WorkspaceResolver | None = None,
        profile: ACPClientProfile | None = None,
        resolve_session_config: SessionConfigResolver | None = None,
        resolve_permission: PermissionResolver | None = None,
        client_capabilities: ClientCapabilities | None = None,
        resolve_mcp_servers: McpServerResolver | None = None,
        spawn_process: SpawnProcess | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        """Bridge Band rooms to the ACP agent ``config`` launches.

        Args:
            config: The agent command and the bridge's plain settings.
            additional_tools: Custom tools served next to the Band tools.
            workspace_for_room: Maps a room id to its absolute workspace;
                defaults to ``./.band-workspaces/<room-id>``.
            profile: Handles a backend's ACP extension methods.
            resolve_session_config: Picks session config options after each
                new or restored session; exclusive with ``config.model`` and
                ``config.reasoning_effort``.
            resolve_permission: Chooses a permission option per tool call;
                ``None`` approves with the agent's allow option.
            client_capabilities: Capabilities advertised at ``initialize``.
            resolve_mcp_servers: Returns native MCP descriptors for the room's
                workspace once per session setup, after initialize. Replaces
                configured external servers, keeping Band routing. Stdio is
                always accepted; HTTP/SSE require explicit agent support.
            spawn_process: Rejected: a custom transport cannot guarantee one
                process per room.
        """
        super().__init__(
            history_converter=ACPClientHistoryConverter(),
            **features,
        )
        self.config = config
        if not self.model_selection.is_empty and resolve_session_config is not None:
            raise ValueError(
                "set either model/reasoning_effort or resolve_session_config, not both"
            )
        if spawn_process is not None:
            raise ValueError(
                "custom ACP transports cannot guarantee room process isolation"
            )
        self._workspace_for_room = workspace_resolver_for(
            config.cwd, workspace_for_room
        )
        self._custom_tools: list[CustomToolDef] = list(additional_tools or [])
        self._tool_definitions, self._own_tool_names = self._registered_tools()
        self._profile = profile
        self._resolve_session_config = resolve_session_config
        self._resolve_permission = resolve_permission
        self._client_capabilities = client_capabilities
        self._resolve_mcp_servers = resolve_mcp_servers
        self._workspaces = RoomWorkspaces(self._workspace_for_room)
        self._retention = RoomRetention(
            config.retention, settle_grace_s=config.turn_settle_timeout_s
        )
        self._rooms: dict[str, ACPRoom] = {}
        self._backend_turns = BackendTurns[ACPTurn](
            self._cancel_agent_turn, provider=ACP_FAILURE_PROVIDER
        )
        # Outlives the room's sessions; see apply_model_selection.
        self._room_selections: dict[str, ModelSelection] = {}
        self._mcp = SharedBandMCPBackend(self._mcp_settings)

    @property
    def backend_turns(self) -> BackendTurns[ACPTurn]:
        return self._backend_turns

    @property
    def model_selection(self) -> ModelSelection:
        """Applied per session from its live catalog, not checked at start."""
        return ModelSelection(
            model=self.config.model, reasoning_effort=self.config.reasoning_effort
        )

    async def apply_model_selection(
        self, selection: ModelSelection, *, room_id: str
    ) -> None:
        """Switch ``room_id``'s ACP session, checked against its catalog when applied.

        The switch queues behind the room's running and queued prompts, as one
        more turn would; other rooms never wait on it. A live session switches
        now and raises ``BandConfigError`` (an ``ACPConfigError`` naming what
        the session offers, when it rejects ``selection``). A room whose
        process is up without a session or one being set up keeps ``selection``
        for its next session, whose turn fails if its catalog refuses it.
        Only a switch that returns counts: the room keeps that selection for
        the adapter's lifetime, and every
        later session for it starts there until one refuses it. A switch that
        raises may leave the live session partly switched, but later sessions
        start where they would have before it. Other rooms keep the configured
        selection. Refused when ``resolve_session_config`` configures sessions
        instead. An empty ``selection`` changes nothing.
        """
        if self._resolve_session_config is not None:
            raise BandConfigError(
                "a runtime model switch cannot be combined with resolve_session_config"
            )
        if selection.is_empty:
            return
        room = self._rooms.get(room_id)
        if room is None:
            raise _no_live_session(room_id)
        # Owned by the room, so its disposal settles and joins the switch
        # instead of closing the process under it. A closing room refuses the
        # switch here, before it ever runs.
        switch = room.retained.start_owned(partial(self._switch_room, room, selection))
        try:
            await switch
        except (asyncio.CancelledError, RuntimeError) as error:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            if not room.retained.closing:
                raise
            raise _session_ended_mid_switch(room_id) from error

    async def _switch_room(self, room: ACPRoom, selection: ModelSelection) -> None:
        async def require_switchable() -> None:
            # Runs before acquisition: a switch never starts a process.
            if room.live_session is None and not room.awaits_session:
                raise _no_live_session(room.room_id)

        # Settings take the room in turn with its prompts.
        async with room.reservation, room.retained.use(prepare=require_switchable):
            session = room.live_session
            if session is None:
                if not room.awaits_session:
                    raise _no_live_session(room.room_id)
                self._queue_room_selection(room.room_id, selection)
                return
            session_id, runtime = session
            async with runtime.config_lock:
                try:
                    await self._apply_model_selection(runtime, session_id, selection)
                except ACPConfigError:
                    # A session replaced while this switch queued or ran fails
                    # it however the old one answered; that is the cause.
                    self._ensure_session_live(room, session)
                    logger.warning(
                        "Switching room %s to %s failed; its session runs %s",
                        room.room_id,
                        selection,
                        self._running_selection(runtime, session_id),
                    )
                    raise
                self._ensure_session_live(room, session)
                self._remember_room_selection(room.room_id, session_id, runtime)

    @staticmethod
    def _ensure_session_live(room: ACPRoom, session: tuple[str, ACPRuntime]) -> None:
        if room.live_session != session:
            raise _session_ended_mid_switch(room.room_id)

    def _running_selection(
        self, runtime: ACPRuntime, session_id: str
    ) -> ModelSelection:
        return self.locate_model_options(
            runtime.config_options(session_id)
        ).current_selection()

    def _remember_room_selection(
        self, room_id: str, session_id: str, runtime: ACPRuntime
    ) -> None:
        running = self._running_selection(runtime, session_id)
        self._room_selections[room_id] = running
        logger.info(
            "ACP session %s for room %s now runs %s", session_id, room_id, running
        )

    def _queue_room_selection(self, room_id: str, selection: ModelSelection) -> None:
        """Start the room's next session at ``selection``, checked when applied."""
        current = self._room_selection(room_id)
        # Efforts belong to a model, so a new model drops the current effort.
        base = current if selection.model in (None, current.model) else ModelSelection()
        queued = base.model_copy(update=selection.model_dump(exclude_none=True))
        self._room_selections[room_id] = queued
        logger.info("Room %s's next ACP session will run %s", room_id, queued)

    def _room_selection(self, room_id: str) -> ModelSelection:
        return self._room_selections.get(room_id, self.model_selection)

    def locate_model_options(
        self, options: Sequence[SessionConfigOption]
    ) -> ACPModelOptions:
        """Find the session's model and effort selects.

        The default reads the spec-reserved categories; agents that publish
        them under other ids or categories override this.
        """
        return locate_model_options(options)

    def apply_effective_features(self, features: AdapterFeatures) -> None:
        """Rebuild the lazy MCP registration after capability negotiation."""
        super().apply_effective_features(features)
        self._tool_definitions, self._own_tool_names = self._registered_tools()

    def _registered_tools(self) -> tuple[list[ToolDefinition], frozenset[str]]:
        """The tools this adapter registers on the loopback MCP server.

        Band platform tools plus custom tools, computed once at construction
        so MCP registration and tool-name canonicalization share one
        vocabulary.
        """
        definitions = list(
            iter_tool_definitions(
                # Memory is an opt-in enterprise capability; contacts are not
                # gated on Capability.CONTACTS despite the same flag shape —
                # every existing caller (the ACP examples) builds this adapter
                # with no features= and expects contacts to just work, so
                # gating them would silently drop band_list_contacts et al.
                # with no warning (SUPPORTED_CAPABILITIES already covers
                # CONTACTS, so the base class's unsupported-capability warning
                # never fires either way).
                capabilities=self.features.capabilities | {Capability.CONTACTS},
            )
        )
        definitions = filter_tool_schemas(
            definitions,
            self.features,
            get_name=lambda definition: definition.name,
            get_category=lambda definition: get_band_tool_category(definition.name),
        )
        # Resembles OpenCodeAdapter's equivalent vocabulary block but isn't
        # extracted into a shared helper: the two sets serve different
        # consumers (opencode's gates auto-approve/permission matching; this
        # one gates narration canonicalization and includes the legacy alias
        # below) and no longer share the same gating rule either.
        names = frozenset(
            {definition.name for definition in definitions}
            | {get_custom_tool_name(model) for model, _fn in self._custom_tools}
            # The legacy band-mcp <=1.3.1 message-send spelling (band_send_message
            # is already covered via iter_tool_definitions). Without it, an
            # external band-mcp's MCP-prefixed legacy call
            # (band-create_agent_chat_message) would canonicalize to nothing and
            # narrate under the raw prefixed name (turn_effect would still
            # resolve its effect).
            | {LEGACY_SEND_MESSAGE_TOOL}
        )
        return definitions, names

    def _build_runtime(self, room: ACPRoom, generation: int) -> ACPRuntime:
        """The process and connection for one residency of ``room``."""
        return ACPRuntime(
            command=_resolve_launcher(self._spawn_command(room.workspace)),
            env=self._spawn_env(room.room_id),
            cwd=self._spawn_cwd(room.workspace),
            auth_method=self.config.auth_method,
            client_factory=partial(self._runtime_client_factory, room, generation),
            spawn_process=spawn_agent_process,
            client_capabilities=self._client_capabilities,
            use_unstable_protocol=self.config.use_unstable_protocol,
        )

    def _spawn_command(self, workspace: str | None) -> list[str]:
        """The argv to launch the ACP agent subprocess with, for this room's workspace."""
        del workspace
        return list(self.config.command)

    def _spawn_env(self, room_id: str) -> dict[str, str] | None:
        """The configured env over the backend's credentials, or ``None``.

        A backend whose agent keeps state per process environment overrides
        this to give each room its own.
        """
        del room_id
        env = {**self._credential_env(), **(self.config.env or {})}
        return env or None

    def _credential_env(self) -> dict[str, str]:
        """Environment a backend derives from its credential settings."""
        return {}

    def _spawn_cwd(self, workspace: str | None) -> str | None:
        """The subprocess-level cwd to launch the ACP agent with, for this room's workspace."""
        return workspace

    def _runtime_client_factory(
        self, room: ACPRoom, generation: int
    ) -> ACPCollectingClient:
        return BandACPClient(
            profile=self._client_profile(room, generation),
            canonicalize_tool_name=self._canonical_tool_name,
        )

    def _client_profile(
        self, room: ACPRoom, generation: int
    ) -> ACPClientProfile | None:
        """The extension profile for one residency's client."""
        del room, generation
        return self._profile

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        # Agent.start() reuses this instance after cleanup_all(); a stop is
        # terminal for its owner and MCP server, so only a start reopens them.
        if self._retention.stopping:
            await self.cleanup_all()
        self._retention = self._retention.renewed()
        await self._mcp.reopen()
        await super().on_started(agent_name, agent_description)

    def _room(self, room_id: str) -> ACPRoom:
        """The room's holder, registered with the residency owner on first use."""
        room = self._rooms.get(room_id)
        if room is not None:
            return room
        self._retention.require_admission()
        with self._workspaces.claiming(room_id) as workspace:
            room = ACPRoom(room_id=room_id, workspace=workspace)
            room.retained = self._retention.register(
                room_id,
                acquire=partial(self._acquire_room, room),
                release=partial(self._release_room, room),
                dispose=partial(self._dispose_room, room),
            )
        self._rooms[room_id] = room
        return room

    async def _acquire_room(self, room: ACPRoom, generation: int, resume: bool) -> None:
        """Start the room's process; a deliberate wake also restores its
        session exactly.

        The runtime is held before its first await, so a failed or abandoned
        start leaves it for the owner's recovery to close.
        """
        # Unreachable while the owner keeps its contract: it acquires only after
        # a confirmed release. Guards against replacing an unclosed process.
        if room.runtime is not None:
            raise RuntimeError(
                f"Room {room.room_id}'s previous ACP process is not confirmed closed"
            )
        runtime = room.runtime = self._build_runtime(room, generation)
        await runtime.start()
        room.retained.observe(
            ProviderActivity.UNKNOWN,
            generation=generation,
            reason=self.config.retention_unavailable_reason,
        )
        if resume and (session := room.session) is not None:
            await self._restore_retained_session(room, runtime, session)

    async def _release_room(self, room: ACPRoom, cause: ReleaseCause) -> ReleaseOutcome:
        """Close the room's process and keep its session identity."""
        await asyncio.gather(*room.session_closes, return_exceptions=True)
        await self.backend_turns.settled(room.room_id)
        if (runtime := room.runtime) is not None:
            await runtime.stop()
            room.runtime = None
        if room.session is not None:
            room.session.live = False
        return ReleaseOutcome.RELEASED

    async def _dispose_room(self, room: ACPRoom) -> None:
        """Erase the room's session and workspace after its final release."""
        room.session = None
        room.tools = None
        self._workspaces.release(room.room_id)
        if self._rooms.get(room.room_id) is room:
            del self._rooms[room.room_id]

    @asynccontextmanager
    async def _using_room(
        self, room: ACPRoom, tools: AgentToolsProtocol
    ) -> AsyncIterator[ACPRuntime]:
        """Reserve the room, lease it, bind ``tools``, and yield its process.

        The reservation serializes the room's prompts from session setup to
        their actual end. Tools bind under the lease, after earlier
        transitions settle and before acquisition, so a startup never serves
        another prompt's tools.
        """

        async def bind_tools() -> None:
            if self.config.inject_band_tools:
                room.tools = tools

        room.retained.require_admission()
        room.session_waiters += 1
        try:
            async with room.reservation, AsyncExitStack() as lease:
                try:
                    await lease.enter_async_context(
                        room.retained.use(prepare=bind_tools)
                    )
                except TimeoutError as error:
                    await self._report_not_ready(room, tools, error)
                    raise
                if room.runtime is None:
                    raise RuntimeError(f"Room {room.room_id} has no ACP process")
                yield room.runtime
        finally:
            room.session_waiters -= 1
            setup = room.session_setup
            if not room.session_waiters and room.setup_observer is not None:
                room.setup_observer.abandon()
            if (
                not room.session_waiters
                and not room.retained.closing
                and setup is not None
                and not setup.done()
                and setup is not room.abandoned_setup
            ):
                # A setup already settling its own cancellation is abandoned
                # too: the callback's timeout may clear that request later.
                room.abandoned_setup = setup
                if not setup.cancelling():
                    setup.cancel()

    @staticmethod
    async def _report_not_ready(
        room: ACPRoom, tools: AgentToolsProtocol, error: TimeoutError
    ) -> None:
        """Fail the turn by name: the room's process did not start or stop
        within the owner's bound, and recovery owns what is left of it. The
        turn's own error log carries the traceback."""
        logger.warning("Room %s: ACP agent process not ready: %s", room.room_id, error)
        await tools.send_failure(
            AgentFailure(ACP_FAILURE_PROVIDER, PROCESS_NOT_READY_ERROR)
        )

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        history: ACPClientSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        room = self._room(room_id)
        await self.backend_turns.settled(room_id)

        async def prompt_room() -> None:
            async with self._using_room(room, tools) as runtime:
                await self._prompt_room(
                    room,
                    runtime,
                    messages,
                    tools,
                    history,
                    participants_msg,
                    contacts_msg,
                    is_session_bootstrap=is_session_bootstrap,
                )

        await room.retained.start_owned(prompt_room)

    async def _prompt_room(
        self,
        room: ACPRoom,
        runtime: ACPRuntime,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        history: ACPClientSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
    ) -> None:
        """Prompt the room's session under the caller's lease; a failure that
        leaves the process unusable requests its recovery."""
        room_id, generation = room.room_id, room.generation
        try:
            session_id = await self._get_or_create_session(
                room,
                runtime,
                history if is_session_bootstrap else None,
            )
        except ACPConfigError as error:
            # An unanswered set may have left a dead connection; only a fresh
            # process lets the next turn retry.
            if isinstance(error, ACPConfigUnreachableError):
                room.retained.recover(generation=generation, reason=str(error))
            await self._report_config_error(tools, error)
            return
        except Exception as error:
            await self._fail_turn(room, generation, tools, error)
            raise
        runtime.reset_session(session_id)

        # A just-created session holds no remote context (a restored one does),
        # so seed it with the Band room's transcript. On bootstrap the converter
        # carried it; a session minted later (the previous process was torn down
        # mid-run) re-fetches it.
        session = self._live_room_session(room)
        replay: list[str] | None = None
        if session.seed.replay_due:
            replay = (
                history.replay_messages
                if is_session_bootstrap
                else await self._fetch_replay(tools, room_id=room_id, messages=messages)
            )

        prompt_text = self._build_prompt_text(
            room_id=room_id,
            session=session,
            messages=messages,
            replay=replay,
            participants_msg=participants_msg,
            contacts_msg=contacts_msg,
        )

        # The emitter posts the turn's events live, in the order the ACP stream
        # delivers them (see RoomTurnEmitter), so narration stays interleaved with
        # the permission pair and any in-room tool post. On failure it drops
        # the text it holds and the error is handled below.
        accepted = True
        try:
            async with RoomTurnEmitter(
                tools,
                emit=self.features.emit,
                # Injected Band tools record their own effects in process.
                records_tool_effects=not self.config.inject_band_tools,
                activity_name=self._activity_tool_name,
            ) as emitter:
                self._install_turn_handlers(
                    runtime,
                    emitter=emitter,
                    room_id=room_id,
                    session_id=session_id,
                )
                turn_deadline = asyncio.timeout(self.config.turn_timeout_s)
                async with self.backend_turns.running(
                    room_id, ACPTurn(runtime, session_id)
                ):
                    try:
                        async with turn_deadline:
                            stop_reason = await runtime.prompt_turn(
                                session_id=session_id,
                                prompt_text=prompt_text,
                                sink=emitter,
                            )
                            if stop_reason == REFUSAL_STOP:
                                logger.warning(
                                    "Room %s: ACP agent refused the prompt",
                                    room_id,
                                )
                                emitter.refused()
                            elif stop_reason != CANCELLED_STOP:
                                session.seed.confirm()
                    except RequestError as error:
                        if is_session_busy(error):
                            raise InputNotAccepted("ACP session is busy") from error
                        raise
                    except TimeoutError:
                        if not turn_deadline.expired():
                            raise
                        await self._await_shielded(
                            asyncio.create_task(
                                self._handle_turn_timeout(
                                    room,
                                    generation=generation,
                                    session_id=session_id,
                                    tools=tools,
                                )
                            )
                        )
                        raise ACPTurnTimeoutError(
                            f"ACP turn timed out after {self.config.turn_timeout_s}s"
                        ) from None
        except DeliveryFailedError as e:
            # The turn's reply is what failed to post -- Band-side delivery,
            # never an ACP provider failure, so the connection stays up.
            reraise_delivery_cause(e)
        except InputNotAccepted:
            # The agent took none of the prompt; its session awaits the seed.
            accepted = False
            raise
        except ACPTurnTimeoutError:
            raise
        except Exception as error:
            await self._fail_turn(room, generation, tools, error)
            raise
        finally:
            if accepted:
                self._settle_seed(room, runtime, session, generation)
        await ACP_CLIENT_RESUME_RECORD.post(
            tools,
            room_id=room_id,
            seed=session.seed,
            session=session_id,
        )

    def _settle_seed(
        self, room: ACPRoom, runtime: ACPRuntime, session: RoomSession, generation: int
    ) -> None:
        """Drop a fresh session whose seeding prompt did not complete.

        Apply the shared resume rule on every accepted prompt's exit, including
        cancellation during timeout recovery or stream flushing. A process
        under recovery goes down with its sessions, so only one that stays up
        is asked to close this one.
        """
        if not (session.seed.replay_due and room.session is session):
            return
        if room.retained.has_authority(generation):
            self._retire_session(
                room, runtime, session, reason=SessionCloseReason.UNSEEDED
            )
        else:
            room.session = None

    @staticmethod
    async def _fail_turn(
        room: ACPRoom,
        generation: int,
        tools: AgentToolsProtocol,
        error: Exception,
    ) -> None:
        """Report an agent failure and retire the residency it left unusable."""
        logger.exception(
            "ACP agent error (room=%s, generation=%s); recovering its process",
            room.room_id,
            generation,
        )
        room.retained.recover(generation=generation, reason=f"ACP agent error: {error}")
        await tools.send_failure(_to_agent_failure(error))

    @staticmethod
    def _live_room_session(room: ACPRoom) -> RoomSession:
        session = room.live_record
        if session is None:
            raise RuntimeError(f"Room {room.room_id} has no live ACP session")
        return session

    async def _handle_turn_timeout(
        self,
        room: ACPRoom,
        *,
        generation: int,
        session_id: str,
        tools: AgentToolsProtocol,
    ) -> None:
        """Cancel and report a prompt that exceeded the adapter timeout; its
        process is recovered once the turn unwinds."""
        logger.error(
            "ACP turn timed out after %ss (room=%s, session=%s)",
            self.config.turn_timeout_s,
            room.room_id,
            session_id,
        )
        await self.backend_turns.stop(room.room_id)
        room.retained.recover(generation=generation, reason="ACP turn timed out")
        await tools.send_failure(
            AgentFailure(
                ACP_FAILURE_PROVIDER,
                f"ACP agent response timed out after {self.config.turn_timeout_s}s",
                FAILURE_CODE_TIMEOUT,
            )
        )

    @staticmethod
    async def _await_shielded(task: asyncio.Task[None]) -> None:
        """Wait for ``task`` through outer cancels without cancelling it."""
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            raise

    @staticmethod
    async def _cancel_agent_turn(turn: ACPTurn) -> None:
        """Tell the agent to stop this room's prompt; best effort."""
        try:
            await turn.runtime.cancel_turn(turn.session_id)
        except Exception:
            logger.exception(
                "ACP turn cancellation failed (session=%s)", turn.session_id
            )

    def _install_turn_handlers(
        self,
        runtime: ACPRuntime,
        *,
        emitter: RoomTurnEmitter,
        room_id: str,
        session_id: str,
    ) -> None:
        runtime.set_permission_handler(
            session_id,
            self._make_permission_handler(emitter, room_id),
        )
        elicitation_handler = self._make_elicitation_handler(
            emitter, room_id, session_id
        )
        if elicitation_handler is not None:
            runtime.set_elicitation_handler(session_id, elicitation_handler)

    def _make_elicitation_handler(
        self,
        emitter: RoomTurnEmitter,
        room_id: str,
        session_id: str,
    ) -> ElicitationHandler | None:
        del emitter, room_id, session_id
        return None

    def _make_permission_handler(
        self,
        emitter: RoomTurnEmitter,
        room_id: str,
    ) -> PermissionHandler:
        async def handler(
            options: object,
            session_id: str,
            tool_call: object,
            narrate_permission: PermissionNarrator | None = None,
            **kwargs: object,
        ) -> dict[str, object]:
            del kwargs
            call = ACPToolCall.from_acp(
                tool_call, canonicalize=self._canonical_tool_name
            )

            option_id = await self._resolve_permission_option(
                call=call,
                options=options,
                room_id=room_id,
                session_id=session_id,
            )

            logger.info(
                "Permission request: %s",
                "selected" if option_id is not None else "cancelled",
            )

            if option_id is not None:
                return allow_permission(option_id)

            await self._narrate_cancelled_permission(
                call=call,
                session_id=session_id,
                emitter=emitter,
                narrate=narrate_permission,
            )
            return cancel_permission()

        return handler

    @staticmethod
    async def _narrate_cancelled_permission(
        *,
        call: ACPToolCall,
        session_id: str,
        emitter: RoomTurnEmitter,
        narrate: PermissionNarrator | ElicitationNarrator | None,
    ) -> None:
        """Post a cancelled-permission narration, serialized under the caller's
        session narrator when one is given (permission and elicitation share
        this shape; only the wire response each caller returns differs)."""
        narration = emitter.open_permission(
            call=call,
            session_id=session_id,
            outcome="cancelled",
        )
        if narrate is None:
            await narration
        else:
            await narrate(narration)

    async def _resolve_permission_option(
        self,
        *,
        call: ACPToolCall,
        options: object,
        room_id: str,
        session_id: str,
    ) -> str | None:
        """Return a validated permission choice, or cancel the request."""
        if self._resolve_permission is None:
            return select_allow_option_id(options)

        offered = self._permission_options(options)
        option_id = await self._resolve_permission(
            ACPPermissionRequest(
                room_id=room_id,
                session_id=session_id,
                tool_call=call,
                options=offered,
            )
        )
        if option_id is None:
            return None
        if not isinstance(option_id, str):
            raise ValueError("ACP permission resolver must return an option id or None")
        if option_id not in self._permission_option_ids(offered):
            raise ValueError(
                f'ACP permission resolver selected unavailable option "{option_id}".'
            )
        return option_id

    @staticmethod
    def _permission_options(options: object) -> tuple[PermissionOptionValue, ...]:
        """Return recognized ACP permission choices without fabricating any."""
        if not isinstance(options, (list, tuple)):
            return ()
        return tuple(
            option
            for option in options
            if isinstance(option, (PermissionOption, Mapping))
        )

    @staticmethod
    def _permission_option_ids(options: tuple[PermissionOptionValue, ...]) -> set[str]:
        """The wire option ids a resolver may select."""
        return set(permission_option_ids(options))

    def _build_system_context(self, room_id: str) -> str:
        agent_name = self.agent_name or "Agent"
        agent_desc = self.agent_description or "An AI assistant"

        system_prompt = render_system_prompt(
            agent_name=agent_name,
            agent_description=agent_desc,
            custom_section=self.config.custom_section,
            include_base_instructions=False,
            features=self.features,
        )

        # Injected Band tools are bound to this room by their endpoint; only
        # an external Band MCP server still takes the room as an argument.
        room_line, room_hint = "", ""
        if not self.config.inject_band_tools:
            room_line = f"Current {CHAT_ID_FIELD_NAME}: {room_id}\n"
            room_hint = (
                f" When a tool needs the current room, use the Current "
                f"{CHAT_ID_FIELD_NAME} value above."
            )
        room_context = (
            f"\n## Room Context\n"
            f"You are connected to Band using the Band tools.\n"
            f"{COMMUNICATION_INSTRUCTIONS}\n\n"
            f"{TURN_COMPLETION_GUIDANCE}\n"
            f"\n"
            f"{room_line}"
            f"{SENDER_METADATA_INSTRUCTION}\n"
            f"\n"
            f"Use each MCP tool's schema for its argument names.{room_hint}\n"
        )

        return f"{SYSTEM_CONTEXT_HEADER}\n{system_prompt}\n{room_context}"

    def _build_local_mcp_server_config(
        self, backend: BandMCPBackend, transport: BandMCPTransport, room_id: str
    ) -> LocalMcpServerConfig:
        url = backend.endpoint(transport, room_id)
        # Sent over the ACP stdio connection, never the agent's argv or env.
        headers = [
            HttpHeader(name=name, value=value)
            for name, value in backend.headers.items()
        ]
        match transport:
            case BandMCPTransport.SSE:
                return SseMcpServer(
                    type="sse", name=BAND_MCP_SERVER_NAME, url=url, headers=headers
                )
            case BandMCPTransport.HTTP:
                return HttpMcpServer(
                    type="http", name=BAND_MCP_SERVER_NAME, url=url, headers=headers
                )

    def _canonical_tool_name(self, name: str) -> str:
        """Strip an MCP server prefix off one of our own tools.

        Mirrors the opencode adapter: only a name that reveals a tool this
        adapter registered is rewritten; anything else passes through.
        """
        return canonicalize_mcp_tool_name(name, self._own_tool_names)

    def _activity_tool_name(self, call: ACPToolActivity) -> str:
        """Report only an identity from this adapter's Band registration."""
        if (
            self.config.inject_band_tools
            and not call.mcp_metadata_rejected
            and call.mcp_server == BAND_MCP_SERVER_NAME
            and any(
                definition.name == call.mcp_tool
                for definition in self._tool_definitions
            )
        ):
            assert call.mcp_tool is not None
            return call.mcp_tool
        return UNKNOWN_ACTIVITY_TOOL_NAME

    def _mcp_settings(self) -> BandMCPBackendSettings:
        return BandMCPBackendSettings(
            tool_definitions=self._tool_definitions,
            get_tools=self._bound_tools,
            additional_tools=self._custom_tools,
            room_bound=True,
        )

    def _bound_tools(self, room_id: str) -> AgentToolsProtocol | None:
        """The tools the room's latest prompt bound.

        A Band MCP call names only its room, so a background call that
        outlives its prompt is served with a later prompt's tools.
        """
        room = self._rooms.get(room_id)
        return None if room is None else room.tools

    def _retire_session(
        self,
        room: ACPRoom,
        runtime: ACPRuntime,
        session: RoomSession,
        *,
        reason: SessionCloseReason,
    ) -> None:
        """Forget the room's session and close it in the background.

        The room's next turn creates and seeds a fresh session.
        """
        logger.info(
            "Replacing ACP session %s for room %s (%s)",
            session.session_id,
            room.room_id,
            reason,
        )
        room.session = None
        runtime.reset_session(session.session_id)
        self._close_session_later(room, runtime, session.session_id, reason=reason)

    async def _get_or_start_band_mcp_server(
        self, room_id: str, transport: BandMCPTransport
    ) -> LocalMcpServerConfig:
        backend = await self._mcp.ensure()
        return self._build_local_mcp_server_config(backend, transport, room_id)

    async def _get_or_create_session(
        self,
        room: ACPRoom,
        runtime: ACPRuntime,
        history: ACPClientSessionState | None,
    ) -> str:
        """The room's live session id, restored or created when it has none.

        A live session is reused only while it dials the room's current Band
        MCP URL; a stale one is retired for a fresh session. A session kept
        from a recovered process, or one persisted in bootstrap ``history``,
        is reloaded when the agent still has it. A fresh session holds no
        conversation context; the caller owes it a transcript replay.
        """
        self._require_current_runtime(room, runtime)
        mcp = await self._session_mcp_servers(room, runtime)
        self._require_current_runtime(room, runtime)
        session = room.session
        if session is not None and session.live:
            if session.band_url == mcp.band_url:
                return session.session_id
            # A fresh session rather than ``session/load`` with new MCP
            # servers: how an agent treats reloading a session that is still
            # live is undefined. The new session is seeded with the transcript.
            history = None
            self._retire_session(
                room, runtime, session, reason=SessionCloseReason.STALE_BAND_MCP_URL
            )
        setup = room.session_setup
        if setup is not None and (setup is room.abandoned_setup or setup.cancelling()):
            # A callback may still be settling an abandoned setup. Its lease
            # holds the process, and no successor resolves beside it.
            await room.retained.wait_owned(setup)
            if room.abandoned_setup is setup:
                room.abandoned_setup = None
            self._require_current_runtime(room, runtime)
            # A setup that was only settling its own cancellation may still
            # have published a session for the waiters it kept serving.
            joined = room.session
            if joined is not None and joined.live and joined.band_url == mcp.band_url:
                return joined.session_id
            setup = None
        if setup is None or setup.done():
            if room.setup_observer is not None:
                room.setup_observer.abandon()
            setup = room.retained.start_owned(
                partial(self._initialize_session, room, runtime, history, mcp)
            )
            room.session_setup = setup
            room.setup_observer = SessionSetupObserver(setup)
        await room.retained.wait_owned(setup)
        observer = room.setup_observer
        assert observer is not None and observer.task is setup
        return observer.consume()

    def _require_current_runtime(
        self, room: ACPRoom, runtime: ACPRuntime, *, finishing: bool = False
    ) -> None:
        if (
            self._rooms.get(room.room_id) is not room
            or room.runtime is not runtime
            or runtime.closed
            or not (
                room.retained.may_finish(room.generation)
                if finishing
                else room.retained.has_authority(room.generation)
            )
            or _setup_interrupted(room)
        ):
            raise asyncio.CancelledError

    async def _initialize_session(
        self,
        room: ACPRoom,
        runtime: ACPRuntime,
        history: ACPClientSessionState | None,
        mcp: SessionMcpServers,
    ) -> str:
        # The setup has its own lease: losing a waiter must not close the
        # process under another waiter or an abandoned callback's settlement.
        async with room.retained.use():
            self._require_current_runtime(room, runtime)
            mcp = await self._with_resolved_mcp_servers(room, runtime, mcp)
            self._require_current_runtime(room, runtime)
            session = room.session
            restore_id = (
                session.session_id
                if session is not None
                else None
                if history is None
                else history.room_to_session.get(room.room_id)
            )
            restored = None
            if restore_id is not None:
                restored = await self._restore_session(
                    room, runtime, restore_id, mcp, retained=session is not None
                )
            initialized = restored or await self._create_session(room, runtime, mcp)
            self._require_current_runtime(room, runtime, finishing=True)
            room.session = initialized
            return initialized.session_id

    async def _restore_session(
        self,
        room: ACPRoom,
        runtime: ACPRuntime,
        session_id: str,
        mcp: SessionMcpServers,
        *,
        retained: bool,
    ) -> RoomSession | None:
        """Reload and configure ``session_id``, or ``None`` when the agent
        cannot restore it.

        A refused remembered selection reverts, so only this turn fails. A
        ``retained`` id is still the room's and stays its identity through
        that refusal, and its process is recovered: the session is loaded
        there, and loading it again on the same process is undefined. A
        bootstrap candidate is closed instead.
        """
        loaded = await runtime.load_session_response(
            cwd=room.workspace,
            session_id=session_id,
            mcp_servers=mcp.servers,
        )
        if loaded is None:
            logger.info(
                "ACP session %s is unavailable for room %s; using a new session",
                session_id,
                room.room_id,
            )
            return None

        try:
            await self._configure_session(runtime, room.room_id, session_id)
            self._require_setup_result(room, finishing=True)
        except BaseException as error:
            if retained:
                room.retained.recover(
                    generation=room.generation,
                    reason=f"Reloaded ACP session {session_id} is unconfigured: {error!r}",
                )
            else:
                await self._close_session(
                    runtime, session_id, reason=SessionCloseReason.UNCONFIGURED
                )
            raise
        logger.debug("Loaded ACP session mapping: %s -> %s", room.room_id, session_id)
        return RoomSession(
            session_id=session_id,
            band_url=mcp.band_url,
            seed=SessionSeed(restored=True),
        )

    async def _restore_retained_session(
        self, room: ACPRoom, runtime: ACPRuntime, session: RoomSession
    ) -> None:
        """Wake the room's retained session on a new process, exactly.

        The load gets fresh MCP servers and cwd and the acknowledged selection
        is reapplied against the new connection's catalog. A refusal raises
        and keeps both the session identity and the selection; it never falls
        back to a new session or a transcript replay.
        """
        mcp = await self._session_mcp_servers(room, runtime)
        mcp = await self._with_resolved_mcp_servers(room, runtime, mcp)
        self._require_setup_result(room)
        await runtime.restore_session_response(
            cwd=room.workspace,
            session_id=session.session_id,
            mcp_servers=mcp.servers,
        )
        await self._configure_session(
            runtime, room.room_id, session.session_id, keep_refused_selection=True
        )
        self._require_setup_result(room, finishing=True)
        session.band_url = mcp.band_url
        session.live = True

    async def _create_session(
        self, room: ACPRoom, runtime: ACPRuntime, mcp: SessionMcpServers
    ) -> RoomSession:
        """Create and configure a session for one room."""
        async with self._fresh_session(room, runtime, mcp.servers) as session:
            await self._configure_session(runtime, room.room_id, session.session_id)
            self._require_setup_result(room, finishing=True)

        logger.info(
            "Created ACP session %s for room %s (mcp_servers=%d)",
            session.session_id,
            room.room_id,
            len(mcp.servers),
        )
        return RoomSession(session_id=session.session_id, band_url=mcp.band_url)

    @asynccontextmanager
    async def _fresh_session(
        self,
        room: ACPRoom,
        runtime: ACPRuntime,
        mcp_servers: list[object],
    ) -> AsyncIterator[NewSessionResponse]:
        """Yield a new session, closing it unless initialization completes."""
        session = await runtime.create_session_response(
            cwd=room.workspace,
            mcp_servers=mcp_servers,
        )
        try:
            yield session
        except asyncio.CancelledError:
            self._close_session_later(
                room,
                runtime,
                session.session_id,
                reason=SessionCloseReason.UNCONFIGURED,
            )
            raise
        except BaseException:
            await self._close_session(
                runtime, session.session_id, reason=SessionCloseReason.UNCONFIGURED
            )
            raise

    async def _close_session(
        self, runtime: ACPRuntime, session_id: str, *, reason: SessionCloseReason
    ) -> None:
        """Best-effort close of a session no room will use again."""
        try:
            await asyncio.wait_for(
                runtime.close_session(session_id),
                timeout=SESSION_CLOSE_TIMEOUT_SECONDS,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except TimeoutError:
            logger.warning(
                "Timed out closing ACP session %s (%s) after %s seconds",
                session_id,
                reason,
                SESSION_CLOSE_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning(
                "Could not close ACP session %s (%s)",
                session_id,
                reason,
                exc_info=True,
            )

    def _close_session_later(
        self,
        room: ACPRoom,
        runtime: ACPRuntime,
        session_id: str,
        *,
        reason: SessionCloseReason,
    ) -> None:
        """Close a retired session without delaying its caller; the room's
        release waits for it before closing the process."""
        task = spawn_owned(
            self._close_session(runtime, session_id, reason=reason),
            label=f"ACP session {session_id} close",
        )
        room.session_closes.add(task)
        task.add_done_callback(room.session_closes.discard)

    async def _session_mcp_servers(
        self, room: ACPRoom, runtime: ACPRuntime
    ) -> SessionMcpServers:
        """The MCP configuration supplied when creating or loading a session."""
        servers: list[object] = list(self.config.mcp_servers)
        if not self.config.inject_band_tools:
            return SessionMcpServers(servers=servers, band_url=None)
        band_server = await self._get_or_start_band_mcp_server(
            room.room_id, runtime.agent_mcp_transport
        )
        return SessionMcpServers(
            servers=[*servers, band_server], band_url=band_server.url
        )

    @staticmethod
    def _require_setup_result(room: ACPRoom, *, finishing: bool = False) -> None:
        # MCP resolution cannot start a new scope after teardown. Configuration
        # already accepted on a resident scope may finish while its lease holds.
        refused = room.retained.closing and not (
            finishing and room.retained.may_finish(room.generation)
        )
        if refused or _setup_interrupted(room):
            raise asyncio.CancelledError

    async def _with_resolved_mcp_servers(
        self, room: ACPRoom, runtime: ACPRuntime, mcp: SessionMcpServers
    ) -> SessionMcpServers:
        resolver = self._resolve_mcp_servers
        if resolver is None:
            return mcp
        resolved = await resolver(room.workspace)
        self._require_setup_result(room)
        if isinstance(resolved, str | bytes) or not isinstance(resolved, Sequence):
            raise TypeError("resolve_mcp_servers must return a sequence of MCP servers")
        reserved = {BAND_MCP_SERVER_NAME}
        external: list[object] = []
        for value in resolved:
            server = self._resolved_mcp_server(runtime, value, reserved)
            reserved.add(server.name)
            # A restore and its new-session fallback share this snapshot even
            # when the callback's owner mutates its original nested values.
            external.append(server.model_copy(deep=True))
        band = [
            server
            for server in mcp.servers
            if getattr(server, "name", None) == BAND_MCP_SERVER_NAME
        ]
        return SessionMcpServers(servers=[*external, *band], band_url=mcp.band_url)

    @staticmethod
    def _resolved_mcp_server(
        runtime: ACPRuntime, server: object, reserved: set[str]
    ) -> ResolvedMcpServer:
        match server:
            case McpServerStdio():
                transport, supported = "stdio", True
            case HttpMcpServer():
                transport, supported = "http", runtime.agent_mcp_http
            case SseMcpServer():
                transport, supported = "sse", runtime.agent_mcp_sse
            case AcpMcpServer():
                raise ACPMcpServerResolutionError(
                    f"MCP server {server.name!r}: resolve_mcp_servers does not "
                    "accept the acp transport"
                )
            case _:
                raise TypeError(
                    "resolve_mcp_servers returned a "
                    f"{type(server).__name__}, not an MCP server descriptor"
                )
        # Descriptor values can contain credentials; diagnostics disclose
        # only the server's name and the refused transport.
        if not supported:
            raise ACPMcpServerResolutionError(
                f"MCP server {server.name!r}: the ACP agent does not advertise "
                f"{transport} MCP servers"
            )
        if server.name in reserved:
            raise ACPMcpServerResolutionError(
                f"MCP server {server.name!r} ({transport}) duplicates a server "
                "name already in this session"
            )
        return server

    async def _configure_session(
        self,
        runtime: ACPRuntime,
        room_id: str,
        session_id: str,
        *,
        keep_refused_selection: bool = False,
    ) -> None:
        """Apply caller-selected values from the session's live ACP catalog.

        ``keep_refused_selection`` keeps a room's switched selection when the
        session refuses it, for a wake that must restore it rather than revert.
        """
        selection = self._room_selection(room_id)
        if not selection.is_empty:
            if keep_refused_selection:
                await self._apply_model_selection(runtime, session_id, selection)
            else:
                await self._apply_room_selection(
                    runtime, room_id, session_id, selection
                )
        elif self._resolve_session_config is not None:
            await self._apply_resolved_config(
                resolver=self._resolve_session_config,
                room_id=room_id,
                session_id=session_id,
                runtime=runtime,
            )

    async def _apply_room_selection(
        self,
        runtime: ACPRuntime,
        room_id: str,
        session_id: str,
        selection: ModelSelection,
    ) -> None:
        try:
            await self._apply_model_selection(runtime, session_id, selection)
        except ACPConfigUnreachableError:
            raise
        except ACPConfigError:
            # A remembered switch the agent no longer accepts must fail this
            # turn only; later sessions go back to the configured selection.
            if self._room_selections.pop(room_id, None) is not None:
                logger.warning(
                    "Room %s's switched model was refused; reverting to %s",
                    room_id,
                    self.model_selection,
                )
            raise

    async def _apply_model_selection(
        self, runtime: ACPRuntime, session_id: str, selection: ModelSelection
    ) -> None:
        await apply_model_selection(
            session_id=session_id,
            catalog=partial(runtime.config_options, session_id),
            selection=selection,
            locate=self.locate_model_options,
            set_option=_config_setter(runtime),
        )

    async def _apply_resolved_config(
        self,
        *,
        resolver: SessionConfigResolver,
        room_id: str,
        session_id: str,
        runtime: ACPRuntime,
    ) -> None:
        """Apply what the caller's ``resolve_session_config`` selects."""
        catalog = runtime.config_options(session_id)
        try:
            selections = await resolver(
                ACPConfigRequest(
                    room_id=room_id,
                    session_id=session_id,
                    config_options=catalog,
                )
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as error:
            raise ACPConfigError(
                session_id=session_id,
                option_id=RESOLVER_CONFIG_OPTION_ID,
                selected_value="",
                message=f"ACP session configuration resolver failed: {error}",
            ) from error
        if selections is None:
            return

        await apply_session_config_selections(
            session_id=session_id,
            config_options=catalog,
            selections=selections,
            set_option=_config_setter(runtime),
        )

    async def _report_config_error(
        self,
        tools: AgentToolsProtocol,
        error: ACPConfigError,
    ) -> None:
        logger.warning(
            "%s%s (session %s, option %s, value %s)",
            CONFIG_FAILURE_PREFIX,
            error,
            error.session_id,
            error.option_id,
            error.selected_value,
        )
        await tools.send_failure(
            AgentFailure(
                ACP_FAILURE_PROVIDER,
                f"{CONFIG_FAILURE_PREFIX}{error}",
                "acp_session_config",
                {
                    "session_id": error.session_id,
                    "option_id": error.option_id,
                    "selected_value": error.selected_value,
                },
            )
        )

    def _system_update_sections(
        self, participants_msg: str | None, contacts_msg: str | None
    ) -> list[str]:
        """Roster/contacts updates as ``[System]`` blocks.

        They arrive only on change (the runtime marks them sent), so inject
        them on whichever turn carries them — mirrors codex and opencode.
        """
        return system_updates(participants_msg, contacts_msg)

    @staticmethod
    def _framed_replay(replay: list[str], live_message: str) -> list[str]:
        """The replay block plus the live message under the nonce'd boundary
        marker the header names (on ordinary turns it needs none)."""
        marker = new_message_marker()
        return [
            HISTORY_REPLAY_HEADER.format(marker=marker) + "\n" + "\n".join(replay),
            f"{marker}\n{live_message}",
        ]

    def _build_prompt_text(
        self,
        *,
        room_id: str,
        session: RoomSession,
        messages: Sequence[PlatformMessage],
        replay: list[str] | None = None,
        participants_msg: str | None = None,
        contacts_msg: str | None = None,
    ) -> str:
        """Add room context, and the transcript replay if one is due, on the
        first prompt sent to an ACP session. The current inputs always come
        last, so the model answers them rather than the replayed history."""
        # Every prompt, not only the first, carries each input's own sender, so
        # in a multi-party room the model always knows who is speaking now and,
        # on replay turns, where the transcript ends and the live inputs begin.
        live_message = format_current_inputs(messages)
        system_updates = self._system_update_sections(participants_msg, contacts_msg)

        if session.seed.seeded:
            return "\n\n".join([*system_updates, live_message])

        sections = [self._build_system_context(room_id), *system_updates]
        if replay:
            sections.extend(self._framed_replay(replay, live_message))
            logger.info(
                "Replaying %d room history lines into new ACP session %s for room %s",
                len(replay),
                session.session_id,
                room_id,
            )
        else:
            sections.append(live_message)
        return "\n\n".join(sections)

    async def on_cleanup(self, room_id: str) -> None:
        """Dispose the room: settle its work, close its process for good, then
        erase its session and workspace.

        Raises:
            CleanupIncompleteError: The disposal outlived the transition
                timeout, or was requested by the room's own work; the room keeps
                its process and a retry joins the same disposal.
        """
        room = self._rooms.get(room_id)
        if room is not None:
            await room.retained.dispose()
        logger.debug("Cleaned up ACP client resources for room %s", room_id)

    async def cleanup_all(self) -> None:
        """Adapter-wide teardown — the hook ``Agent.stop()`` invokes on shutdown.

        Disposes every room, then stops the shared Band MCP server, within one
        ``retention.transition_timeout_s`` budget. Anything unfinished stays
        owned and the next call resumes it. The MCP server stops only after
        every room's process closed. Later turns are refused until
        ``on_started`` serves again.

        Raises:
            CleanupIncompleteError: A room or the MCP server is still closing.
        """
        await self._mcp.close_after(
            self._retention.stop(), retention=self.config.retention
        )
        await self.backend_turns.drain()
        logger.info("ACP client adapter stopped")

    async def stop(self) -> None:
        """Framework teardown only; registered cleanup waits for host stop/shutdown."""
        await self.cleanup_all()

    async def _fetch_replay(
        self,
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        messages: Sequence[PlatformMessage],
    ) -> list[str] | None:
        """The room transcript for a session created off-bootstrap.

        The runtime hands history to the adapter only on session bootstrap;
        when a session is minted later (the previous runtime was torn down
        mid-run), the transcript is re-fetched so the fresh session does not
        start amnesiac. Entries from the turn's earliest input onward are
        excluded: they are this turn and pending turns of their own.
        """
        try:
            context = await tools.fetch_room_context(room_id=room_id)
            raw = messages_before(context.get("data") or [], messages)
        except Exception:
            logger.warning(
                "Room %s: could not fetch history to re-seed the new ACP session",
                room_id,
                exc_info=True,
            )
            return None
        return build_replay_messages(raw)


def _no_live_session(room_id: str) -> BandConfigError:
    return BandConfigError(f"room {room_id} has no live ACP session to switch")


def _session_ended_mid_switch(room_id: str) -> BandConfigError:
    return BandConfigError(f"room {room_id}'s ACP session ended mid-switch")
