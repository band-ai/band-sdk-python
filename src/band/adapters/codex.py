"""Codex app-server adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, fields
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from typing import Any, ClassVar, Literal, NamedTuple, Protocol

from band_sdk_core import AgentFailure
from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    ValidationError,
    field_validator,
)
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from typing_extensions import Unpack

from band.converters.codex import CodexHistoryConverter
from band.converters.helpers import build_replay_messages
from band.core.adapterconfig import EnvAdapterConfig
from band.core.content import has_visible_content
from band.core.defaultmodels import OPENAI_MODEL
from band.core.delivery import (
    DeliveryFailedError,
    deliver_notice,
    reraise_delivery_cause,
)
from band.core.exceptions import InputNotAccepted
from band.core.protocols import (
    FAILURE_CODE_TIMEOUT,
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    AgentToolsProtocol,
    TurnResultAlreadyReported,
    send_event_safe,
)
from band.core.retention import (
    ReleaseCause,
    ReleaseOutcome,
    RetainedRoom,
    RoomRetention,
    RoomRetentionConfig,
    require_disabled_retention,
)
from band.core.retention.cleanup import spawn_owned
from band.core.simple_adapter import SimpleAdapter
from band.core.tool_filter import filter_tool_schemas
from band.core.turn import judge_detached_turn
from band.core.turn_lifecycle import ApprovalInterruptMixin, TurnTask, bind_turn_task
from band.core.types import (
    AgentInput,
    ApprovalMode,
    Capability,
    Emit,
    FeatureKwargs,
    PlatformMessage,
    ToolEventKey,
    TurnUsage,
)
from band.integrations.codex import (
    TRANSPORT_CLOSED,
    CodexJsonRpcError,
    CodexStdioClient,
    RpcEvent,
)
from band.integrations.codex.types import (
    CODEX_APPROVAL_METHODS,
    CODEX_PROVIDER,
    DYNAMIC_TOOL_CALL_METHOD,
    ApprovalAuditEntry,
    CodexApprovalMethod,
    CodexItemType,
    CodexRequestMethod,
    CodexSessionState,
    CodexTokenUsage,
    build_agent_failure,
    parse_plan_steps,
)
from band.runtime.custom_tools import (
    CustomToolDef,
    custom_tool_to_openai_schema,
    execute_custom_tool,
    find_custom_tool,
    format_validation_error,
)
from band.runtime.decisions import DecisionEntry, DecisionRegistry, Timeout
from band.runtime.formatters import strip_leading_mentions
from band.runtime.prompts import COMMUNICATION_INSTRUCTIONS, render_system_prompt
from band.runtime.tools import (
    get_band_tool_category,
    image_block_placeholder,
    is_image_passthrough_result,
    redact_tool_call_args,
)
from band.workspaces import (
    RoomWorkspaces,
    WorkspaceResolver,
    is_host_absolute,
)

logger = logging.getLogger(__name__)


def _image_content_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    """An image tool result as Codex app-server ``inputImage`` content items.

    Inlined as a data: URI -- the protocol's ``imageUrl`` is a bare string with
    no documented inline-vs-http distinction.
    """
    return [
        {
            "type": "inputImage",
            "imageUrl": f"data:{block['mimeType']};base64,{block['data']}",
        }
        for block in result["content"]
    ]


TransportKind = Literal["stdio", "ws"]
ApprovalDecision = Literal["accept", "acceptForSession", "decline"]
_REASONING_SUMMARIES = {"auto", "concise", "detailed", "none"}

# Codex-local slash commands, which surface their outcome in the room themselves.
_SILENT_REPORTING_TOOLS: frozenset[str] = frozenset({"setmodel", "setreasoning"})


class CodexCommand(StrEnum):
    """The `/<word>` room commands this adapter handles itself, rather than
    forwarding to Codex -- the single source for parsing, dispatch, and the
    command names quoted back in room messages."""

    HELP = "help"
    STATUS = "status"
    MODEL = "model"
    MODELS = "models"
    REASONING = "reasoning"
    APPROVALS = "approvals"
    APPROVE = "approve"
    APPROVE_SESSION = "approve-session"
    DECLINE = "decline"
    SANDBOX = "sandbox"
    PERMISSIONS = "permissions"
    THREADS = "threads"
    THREAD = "thread"
    USAGE = "usage"


# The decision each resolving approval command sends back to Codex.
_APPROVAL_COMMAND_DECISIONS: dict[CodexCommand, ApprovalDecision] = {
    CodexCommand.APPROVE: "accept",
    CodexCommand.APPROVE_SESSION: "acceptForSession",
    CodexCommand.DECLINE: "decline",
}
_APPROVAL_COMMANDS = frozenset({*_APPROVAL_COMMAND_DECISIONS, CodexCommand.APPROVALS})
# Local commands that ask the room's Codex process.
_PROCESS_COMMANDS = frozenset(
    {CodexCommand.MODEL, CodexCommand.MODELS, CodexCommand.REASONING}
)
# Membership by plain string: Python 3.11's Enum rejects `"word" in CodexCommand`.
_COMMAND_WORDS = frozenset(CodexCommand)

APPROVAL_REQUESTED_TEMPLATE = (
    "Approval requested ({summary}). Approval id: `{token}`. "
    f"Reply `/{CodexCommand.APPROVE} {{token}}` or "
    f"`/{CodexCommand.DECLINE} {{token}}` or "
    f"`/{CodexCommand.APPROVE_SESSION} {{token}}` "
    "(approve all similar for this session). "
    f"Use `/{CodexCommand.APPROVALS}` to list pending approvals."
)
APPROVAL_RESOLVED_TEMPLATE = "Approval `{token}` resolved as `{decision}`."
APPROVAL_TIMED_OUT_TEMPLATE = "Approval `{token}` timed out. Applied `{decision}`."
NO_APPROVALS_TO_RESOLVE_MESSAGE = "No pending approvals to resolve."


class CodexSubcommand(StrEnum):
    """Second words of `/model` and `/thread`."""

    LIST = "list"
    LS = "ls"
    INFO = "info"
    ARCHIVE = "archive"


_MODEL_LIST_WORDS = frozenset({CodexSubcommand.LIST, CodexSubcommand.LS})


class CodexSandboxMode(StrEnum):
    """thread/start's kebab-case ``SandboxMode`` values -- also exactly what
    ``/sandbox`` accepts."""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


# Membership by plain string: Python 3.11's Enum rejects `"word" in CodexSandboxMode`.
_SANDBOX_MODES = frozenset(CodexSandboxMode)
# A sandbox kind expressible only as a turn/start sandboxPolicy, not a mode.
_EXTERNAL_SANDBOX_KEY = "external-sandbox"
_EXTERNAL_SANDBOX_POLICY_TYPE = "externalSandbox"
# Config spellings accepted without the dashes, e.g. "readonly".
_SANDBOX_KEY_ALIASES = {
    key.replace("-", ""): key for key in (*CodexSandboxMode, _EXTERNAL_SANDBOX_KEY)
}
_SANDBOX_CONFIRM_FLAG = "--confirm"
_SANDBOX_MODE_CHOICES = "|".join(CodexSandboxMode)

_HELP_TEXT = (
    "Codex commands: "
    f"`/{CodexCommand.STATUS}`, `/{CodexCommand.MODEL}`, `/{CodexCommand.MODELS}`, "
    f"`/{CodexCommand.MODEL} {CodexSubcommand.LIST}`, "
    f"`/{CodexCommand.MODELS} {CodexSubcommand.LIST}`, `/{CodexCommand.MODEL} <id>`, "
    f"`/{CodexCommand.REASONING} [effort]`, "
    f"`/{CodexCommand.APPROVALS}`, `/{CodexCommand.APPROVE} <id>`, "
    f"`/{CodexCommand.APPROVE_SESSION} <id>`, `/{CodexCommand.DECLINE} <id>`, "
    f"`/{CodexCommand.THREADS}`, `/{CodexCommand.THREAD} {CodexSubcommand.INFO}`, "
    f"`/{CodexCommand.THREAD} {CodexSubcommand.ARCHIVE}`, "
    f"`/{CodexCommand.SANDBOX} <mode>`, `/{CodexCommand.PERMISSIONS}`, "
    f"`/{CodexCommand.USAGE}`, `/{CodexCommand.HELP}`."
)

# Upper bound on cached task titles (room-lifecycle map used to preserve the
# title between task_started and task_complete events).  500 covers bursty
# conversations while keeping memory bounded.
_MAX_TASK_TITLES = 500

# Cap on the raw diff string forwarded in ``turn/diff/updated`` task metadata,
# expressed in UTF-8 bytes.  Diffs can be megabytes on large refactors;
# shipping the full blob inflates WebSocket frames and chat-event storage.
# Consumers that need the full diff should request it from Codex directly.
# Enforcing the cap on encoded bytes (not Python characters) bounds the wire
# size uniformly regardless of whether the diff contains ASCII or heavy
# multi-byte content (emoji, CJK).
_MAX_DIFF_METADATA_BYTES = 64 * 1024

# item/completed "type" values gated on Emit.TOOL_CALLS; dispatched in
# _extract_tool_item.
_TOOL_ITEM_TYPES: frozenset[CodexItemType] = frozenset(
    {
        CodexItemType.COMMAND_EXECUTION,
        CodexItemType.FILE_CHANGE,
        CodexItemType.MCP_TOOL_CALL,
        CodexItemType.WEB_SEARCH,
        CodexItemType.IMAGE_VIEW,
        CodexItemType.COLLAB_AGENT_TOOL_CALL,
    }
)

# Tools Codex asks the adapter to run (item/tool/call). The adapter reports them
# there, with the real result, so their item/completed is the same call again.
_REQUESTED_TOOL_ITEM_TYPES: frozenset[CodexItemType] = frozenset(
    {CodexItemType.DYNAMIC_TOOL_CALL}
)

# The prompt and the reply; the reply's text is read by the turn loop.
_MESSAGE_ITEM_TYPES: frozenset[CodexItemType] = frozenset(
    {CodexItemType.USER_MESSAGE, CodexItemType.AGENT_MESSAGE}
)

# Read only to attribute a sub-agent's requests to its turn; never rendered.
_ROUTING_ITEM_TYPES: frozenset[CodexItemType] = frozenset(
    {CodexItemType.SUB_AGENT_ACTIVITY}
)

_UNRENDERED_ITEM_TYPES = (
    _REQUESTED_TOOL_ITEM_TYPES | _MESSAGE_ITEM_TYPES | _ROUTING_ITEM_TYPES
)

# item/completed "type" values gated on Emit.THOUGHTS; dispatched in
# _extract_thought_text.
_THOUGHT_ITEM_TYPES: frozenset[CodexItemType] = frozenset(
    {
        CodexItemType.REASONING,
        CodexItemType.PLAN,
        CodexItemType.CONTEXT_COMPACTION,
        CodexItemType.ENTERED_REVIEW_MODE,
        CodexItemType.EXITED_REVIEW_MODE,
    }
)


class CodexToolItem(NamedTuple):
    """(name, args, output) for one tool-like item/completed entry."""

    name: str
    args: dict[str, Any]
    output: str


# ---------------------------------------------------------------------------
# Self-configuration tools — let Codex change its own model/reasoning at runtime
# ---------------------------------------------------------------------------


class SetModelInput(BaseModel):
    """Switch the model used for subsequent turns. Call this when a different model would be more appropriate for the task (e.g. a faster model for simple queries, a stronger model for complex reasoning)."""

    model: str = Field(description="Model ID to use (e.g. 'gpt-5.5').")


class SetReasoningInput(BaseModel):
    """Adjust reasoning effort and summary detail for subsequent turns. Use higher effort for complex problems and lower effort for straightforward tasks."""

    effort: str | None = Field(
        default=None,
        description="Reasoning effort level supported by the current model (for example low, medium, or high). Omit to keep current.",
    )
    summary: str | None = Field(
        default=None,
        description="Reasoning summary detail: auto, concise, detailed, or none. Omit to keep current.",
    )


class CodexClientProtocol(Protocol):
    async def connect(self) -> None: ...

    async def initialize(
        self,
        *,
        client_name: str,
        client_title: str,
        client_version: str,
        experimental_api: bool = False,
        opt_out_notification_methods: list[str] | None = None,
    ) -> dict[str, Any]: ...

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_on_overload: bool = True,
    ) -> dict[str, Any]: ...

    async def recv_event(self, timeout_s: float | None = None) -> RpcEvent: ...

    async def respond(self, request_id: int | str, result: dict[str, Any]) -> None: ...

    async def respond_error(
        self,
        request_id: int | str,
        *,
        code: int,
        message: str,
        data: Any | None = None,
    ) -> None: ...

    async def close(self) -> None: ...


class CodexContinuityError(RuntimeError):
    """A retained Codex thread cannot continue as it was; its id is kept."""


_METHOD_NOT_FOUND = -32601
# How a turn whose Codex process was lost reports its failure.
TRANSPORT_LOST_ERROR = "Codex transport closed unexpectedly"
# How a turn reports a room process that did not start or stop within
# ``retention.transition_timeout_s``.
PROCESS_NOT_READY_ERROR = "Codex process did not become ready in time"
# One acquisition starts the process, refreshes ``model/list`` online and
# strictly resumes the thread's whole rollout.
CODEX_TRANSITION_TIMEOUT_S = 60.0
_UNOWNED_TOOL_CALL_TEXT = (
    "No Band turn started this Codex turn, so Band tools are unavailable to it."
)


def _text_item(text: str) -> dict[str, Any]:
    return {"type": "inputText", "text": text}


def _tool_response(items: list[dict[str, Any]], *, success: bool) -> dict[str, Any]:
    """A dynamic tool call's response."""
    return {"contentItems": items, "success": success}


def _tool_result(text: str, *, success: bool) -> dict[str, Any]:
    """A dynamic tool call's response carrying one text item."""
    return _tool_response([_text_item(text)], success=success)


def _approval_response(decision: ApprovalDecision) -> dict[str, Any]:
    return {"decision": decision}


async def _refuse_unhandled_request(
    client: CodexClientProtocol, *, request_id: int | str, method: str
) -> None:
    await client.respond_error(
        request_id,
        code=_METHOD_NOT_FOUND,
        message=f"Unhandled server request: {method}",
    )


@dataclass
class PendingApproval:
    request_id: int | str
    method: str
    summary: str
    created_at: datetime
    future: asyncio.Future[ApprovalDecision]
    session_key: str = ""


@dataclass
class NativeMessage:
    """One agent-message item's streamed and authoritative completed text."""

    phase: str | None = None
    streamed_text: str = ""
    completed_text: str | None = None

    @property
    def unstreamed_text(self) -> str:
        text = self.completed_text or ""
        if text.startswith(self.streamed_text):
            return text[len(self.streamed_text) :]
        return text


@dataclass
class TurnResult:
    """Aggregated result from processing a single Codex turn's event stream."""

    native_messages: dict[str, NativeMessage] = dataclass_field(default_factory=dict)
    turn_status: str = "failed"
    turn_error: str = ""


def _event_params(event: RpcEvent) -> dict[str, Any]:
    return event.params if isinstance(event.params, dict) else {}


def _native_turn_id(event: RpcEvent) -> str | None:
    """The native turn ``event`` belongs to: its ``turnId``, or the ``turn.id``
    that ``turn/started`` and ``turn/completed`` carry instead."""
    params = _event_params(event)
    turn_id = params.get("turnId")
    if turn_id is None and isinstance(turn := params.get("turn"), dict):
        turn_id = turn.get("id")
    return str(turn_id) if turn_id else None


def _loss_reason(cause: RpcEvent | Exception) -> str:
    """Why a process's events stopped: the transport's stated reason, if any."""
    if isinstance(cause, Exception):
        return repr(cause)
    params = _event_params(cause)
    return str(params.get("reason") or cause.method)


def _thread_id(event: RpcEvent) -> str | None:
    params = _event_params(event)
    thread_id = params.get("threadId")
    return str(thread_id) if thread_id else None


def _delegated_threads(event: RpcEvent) -> set[str]:
    """Sub-agent threads an item says the turn spawned or addressed."""
    item = _event_params(event).get("item")
    if not isinstance(item, dict):
        return set()
    match item.get("type"):
        case CodexItemType.COLLAB_AGENT_TOOL_CALL:
            threads = item.get("receiverThreadIds") or ()
        case CodexItemType.SUB_AGENT_ACTIVITY:
            threads = (item.get("agentThreadId"),)
        case _:
            return set()
    return {str(thread) for thread in threads if thread}


@dataclass
class NativeTurn:
    """The Band turn that started one native turn, and the events addressed to it.

    Codex names the turn only when ``turn/start`` answers, so events that
    arrive first wait in ``early`` until ``turn_id`` is known. Sub-agents
    the turn delegates to ask under their own thread and turn, and Codex
    names their thread only in the turn's own items, so their requests
    wait in ``unattributed`` until one does.
    """

    client: CodexClientProtocol
    generation: int
    thread_id: str | None = None
    turn_id: str | None = None
    # An exception is the reader's own failure, which ends the turn.
    events: asyncio.Queue[RpcEvent | Exception] = dataclass_field(
        default_factory=asyncio.Queue
    )
    early: list[RpcEvent] = dataclass_field(default_factory=list)
    sub_agents: set[str] = dataclass_field(default_factory=set)
    unattributed: list[RpcEvent] = dataclass_field(default_factory=list)
    # The process stopped: its closing notice is queued behind its requests.
    lost: bool = False

    def owns(self, event: RpcEvent) -> bool:
        # Every event a turn handles names its turn; thread-scoped ones do not.
        if _native_turn_id(event) == self.turn_id:
            return True
        # Only a sub-agent's requests: its own turn/completed must not end this turn.
        return event.kind == "request" and _thread_id(event) in self.sub_agents

    def may_own(self, event: RpcEvent) -> bool:
        """A request from another thread, which a later collab item may name."""
        return event.kind == "request" and _thread_id(event) not in (
            None,
            self.thread_id,
        )

    def deliver(self, event: RpcEvent) -> None:
        """Queue an owned event, then the held requests it names a sub-agent for."""
        self.events.put_nowait(event)
        if not (receivers := _delegated_threads(event) - self.sub_agents):
            return
        self.sub_agents |= receivers
        held, self.unattributed = self.unattributed, []
        for request in held:
            if self.owns(request):
                self.events.put_nowait(request)
            else:
                self.unattributed.append(request)

    def claim(self, event: RpcEvent) -> bool:
        """Deliver or hold ``event``; False when this turn can never own it."""
        if self.owns(event):
            self.deliver(event)
        elif self.may_own(event):
            self.unattributed.append(event)
        else:
            return False
        return True


def _result_id(result: Any, key: str) -> str:
    """The ``id`` of ``result[key]``, or "" when Codex returned none."""
    entry = result.get(key) if isinstance(result, dict) else None
    return str(entry.get("id") or "") if isinstance(entry, dict) else ""


def _undelivered(native: NativeTurn) -> list[RpcEvent]:
    """Events still waiting for the native turn when its Band turn ends."""
    queued = [native.events.get_nowait() for _ in range(native.events.qsize())]
    return [
        *native.early,
        *(item for item in queued if isinstance(item, RpcEvent)),
        *native.unattributed,
    ]


def _tools_by_name(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(tool.get("name")): tool for tool in tools}


@dataclass(frozen=True)
class ThreadSettings:
    """Settings Codex reports for a thread that ``turn/start`` does not re-send;
    a restored thread must report the ones it was started with."""

    model_provider: str | None = None
    cwd: str | None = None

    @classmethod
    def reported_by(cls, result: dict[str, Any]) -> ThreadSettings:
        provider, cwd = result.get("modelProvider"), result.get("cwd")
        return cls(
            model_provider=provider if isinstance(provider, str) else None,
            cwd=cwd if isinstance(cwd, str) else None,
        )

    def changes_in(self, restored: ThreadSettings) -> list[str]:
        return [
            f"{setting.name} {retained!r} became {current!r}"
            for setting in fields(self)
            if (retained := getattr(self, setting.name)) is not None
            and (current := getattr(restored, setting.name)) != retained
        ]


@dataclass
class RoomCodexClient:
    """One Band room's Codex process and what its thread was started with.

    ``residency`` owns admission and every physical transition. ``client`` and
    ``reader`` are cleared only once the process is confirmed closed; the
    room's thread id stays in the adapter's ``_room_threads`` across process
    replacement until the room is finally disposed.
    """

    room_id: str
    workspace: str
    residency: RetainedRoom = dataclass_field(init=False)
    client: CodexClientProtocol | None = None
    reader: asyncio.Task[None] | None = None
    turn: NativeTurn | None = None
    model_override: str | None = None
    selected_model: str | None = None
    reasoning_effort: str | None = None
    reasoning_summary: str | None = None
    # The dynamic tools the thread was started with: resume restores these and
    # ignores any others. None when the thread came from Band history.
    registration: list[dict[str, Any]] | None = None
    settings: ThreadSettings = ThreadSettings()
    # The thread was resumed into this process; a turn confirms its tools.
    restored: bool = False
    # Serializes this room's turns, local commands and their preparation; a
    # turn parked on a manual approval blocks only this room. Approval
    # replies are handled without it so they can resolve that wait.
    rpc_lock: asyncio.Lock = dataclass_field(default_factory=asyncio.Lock)

    @property
    def generation(self) -> int:
        """The residency the room's current process belongs to."""
        return self.residency.generation

    def bind_thread(
        self, *, registration: list[dict[str, Any]] | None, settings: ThreadSettings
    ) -> None:
        """Record what the thread this process now runs was started with."""
        self.registration = registration
        self.settings = settings
        self.restored = False

    def forget_thread(self) -> None:
        self.bind_thread(registration=None, settings=ThreadSettings())

    def require_client(self) -> CodexClientProtocol:
        if self.client is None:
            raise RuntimeError(f"Room {self.room_id} has no Codex process")
        return self.client

    async def stop_reader(self) -> None:
        if (reader := self.reader) is None:
            return
        reader.cancel()
        await asyncio.wait({reader})
        self.reader = None


class _WithoutCwdBinding(PydanticBaseSettingsSource):
    """Drops ``cwd``'s implicit env binding from a settings source.

    ``populate_by_name=True`` makes pydantic-settings honor a field's bare
    name as an env alias alongside any explicit ``validation_alias`` -- which
    for a field literally named ``cwd`` means the plain, generically-meaningful
    ``CODEX_CWD`` is always live, however it's aliased. ``cwd`` exists on
    ``CodexAdapterConfig`` only to reject the legacy explicit kwarg (see
    ``CodexAdapter.__init__``), so an ambient ``CODEX_CWD`` set for something
    unrelated (e.g. a Codex CLI convention, or this repo's own E2E harness)
    must never silently populate it and trip that rejection.
    """

    def __init__(self, source: PydanticBaseSettingsSource) -> None:
        super().__init__(source.settings_cls)
        self._source = source

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        return self._source.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        values = dict(self._source())
        values.pop("cwd", None)
        return values


class CodexAdapterConfig(EnvAdapterConfig):
    """Runtime configuration for Codex adapter sessions.

    Every field can be set explicitly (highest priority) or via a
    ``CODEX_``-prefixed environment variable (e.g. ``CODEX_MODEL``,
    ``CODEX_TRANSPORT``, ``CODEX_APPROVAL_MODE``); ``codex_ws_url`` is
    sourced from ``CODEX_WS_URL`` (the field name already carries the
    ``codex_`` part), ``emit_turn_task_markers`` also accepts the legacy
    ``CODEX_TURN_TASK_MARKERS``, and ``codex_command`` is sourced from the
    established ``CODEX_COMMAND`` (not the doubly-prefixed
    ``CODEX_CODEX_COMMAND``), parsed the same way as an explicit tuple: a
    whitespace-split shell string (e.g. ``"custom-codex --args"``). An
    explicit constructor kwarg always wins over the environment.

    Turn task events:
        ``emit_turn_task_markers`` and ``emit_turn_lifecycle_events`` are
        independent channels.  Enabling both will produce **two** task
        events per completed turn: one "Codex turn" marker and one
        enriched "Codex turn lifecycle" event (plus a "started" lifecycle
        event at turn start).  Consumers that render task timelines
        should pick exactly one channel — typically the lifecycle channel
        when its extra metadata is desired.
    """

    # populate_by_name: an aliased field stays constructible by its field name
    # and keeps its prefix-derived environment variable.
    model_config = SettingsConfigDict(
        env_prefix="CODEX_",
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    transport: TransportKind = "stdio"
    retention: RoomRetentionConfig = Field(
        default_factory=lambda: RoomRetentionConfig(
            transition_timeout_s=CODEX_TRANSITION_TIMEOUT_S
        )
    )
    model: str | None = None
    # Not validated here: the supported efforts vary by model and Codex CLI
    # version (model/list reports them), and the backend rejects unknown ones.
    reasoning_effort: str | None = None
    reasoning_summary: Literal["auto", "concise", "detailed", "none"] | None = None
    # Explicit-kwarg-only (see settings_customise_sources): rejected outright
    # in CodexAdapter.__init__ -- never populated from CODEX_CWD.
    cwd: str | None = None
    workspace_for_room: WorkspaceResolver | None = Field(default=None, exclude=True)
    approval_policy: str = "never"
    personality: Literal["friendly", "pragmatic", "none"] = "pragmatic"
    sandbox: str | None = None
    sandbox_policy: dict[str, Any] | None = None
    system_prompt: str | None = None
    custom_section: str = ""
    include_base_instructions: bool = True
    experimental_api: bool = True
    # A validation_alias names an environment variable verbatim (env_prefix is
    # not applied), so aliases must be full CODEX_* names — an unprefixed alias
    # would read a bare env var set for something else entirely.
    emit_turn_task_markers: bool = Field(
        default=False,
        validation_alias=AliasChoices("CODEX_TURN_TASK_MARKERS"),
    )
    approval_mode: ApprovalMode = "manual"
    approval_text_notifications: bool = True
    approval_wait_timeout_s: float = 300.0
    approval_timeout_decision: ApprovalDecision = "decline"
    turn_timeout_s: float = 180.0
    client_name: str = "band_codex_adapter"
    client_title: str = "Band Codex Adapter"
    client_version: str = "0.1.0"
    codex_command: tuple[str, ...] | None = Field(
        default=None,
        validation_alias=AliasChoices("CODEX_COMMAND"),
    )
    codex_env: dict[str, str] | None = None
    codex_ws_url: str = Field(
        default="ws://127.0.0.1:8765",
        validation_alias=AliasChoices("CODEX_WS_URL"),
    )
    enable_self_config_tools: bool = False
    additional_dynamic_tools: list[dict[str, Any]] = Field(default_factory=list)
    # CODEX_SKILL_ROOTS is a JSON list.
    skill_roots: list[str] = Field(default_factory=list)
    inject_history_on_resume_failure: bool = True
    max_history_messages: int = 50
    max_pending_approvals_per_room: int = Field(default=50, ge=1)
    max_approval_audit_per_room: int = 100
    # Upper bound on session-level approvals retained per room
    # (``/approve-session`` patterns).  Evicted LRU-style when exceeded so a
    # long-lived room that approves many distinct commands can't grow this
    # set without bound.
    max_session_approved_per_room: int = 100
    # Upper bound for one attempt at closing the transport client; a close
    # that runs over stays owned and the next cleanup retries it. ``None``
    # disables the bound (legacy behavior); ``retention.transition_timeout_s``
    # still bounds how long a caller waits to learn the outcome.
    client_close_timeout_s: float | None = 10.0
    # Upper bound for letting a still-running turn wind down on its own during
    # a leave or stop (the residency owner's settle grace) before it is
    # cancelled, and for answering a finished turn's leftover native requests.
    # Deliberately not Optional like client_close_timeout_s above: that
    # field's "disable the bound" is a legacy allowance for a rarely-slow
    # transport close, not something a turn possibly stuck on real work
    # should ever be allowed to defeat.
    turn_settle_timeout_s: float = 10.0
    # Matching rules for session-level auto-approval (``/approve-session``).
    #
    # ``"full_command"`` (default, more restrictive) — the key is the exact
    #     command string after ``.strip()`` on the single ``command`` field
    #     of the approval payload.  Matching is byte-exact and case-sensitive;
    #     only leading/trailing whitespace is normalised.  ``npm test`` does
    #     NOT match ``npm  test`` (double space), ``NPM TEST`` (case), or
    #     ``npm test --watch`` (trailing args).
    #
    # ``"binary"`` (less restrictive) — the key is just the first whitespace-
    #     delimited token of the command.  ``/approve-session`` on ``npm test``
    #     auto-approves every future command starting with ``npm`` in that
    #     room, regardless of subcommand or arguments.  Intended for
    #     trusted-binary shortcuts (``npm``, ``git``) where per-argv friction
    #     outweighs the safety margin.  Not recommended for commands that can
    #     take destructive flags (``rm``, ``git push --force``).
    #
    # File-change approvals always key on the sorted set of paths being
    # modified, independent of this flag.
    session_approval_granularity: Literal["binary", "full_command"] = "full_command"
    # --- Phase 2: Plan & task lifecycle ---
    stream_plan_events: bool = False
    emit_turn_lifecycle_events: bool = False
    # --- Phase 3: Real-time streaming ---
    stream_reasoning_events: bool = False
    stream_commentary_events: bool = False
    # --- Phase 4: Diffs & token usage ---
    emit_diff_events: bool = False
    emit_token_usage_events: bool = False

    @field_validator("retention")
    @classmethod
    def _require_safe_retention(
        cls, retention: RoomRetentionConfig
    ) -> RoomRetentionConfig:
        return require_disabled_retention(
            retention,
            reason=(
                "Codex idle retention is unavailable: native idle and terminal state "
                "do not account for asynchronous hooks and delegated work; shutdown "
                "can cancel that work."
            ),
        )

    @field_validator("codex_command", mode="before")
    @classmethod
    def _split_codex_command(cls, value: Any) -> Any:
        """CODEX_COMMAND is a shell string; an explicit tuple/list kwarg
        passes through unchanged."""
        if isinstance(value, str):
            return value.split()
        return value

    @field_validator("skill_roots")
    @classmethod
    def _require_absolute_paths(cls, roots: list[str]) -> list[str]:
        if relative := [root for root in roots if not is_host_absolute(root)]:
            raise ValueError(f"skill_roots must be absolute paths: {relative}")
        return roots

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Keep ``cwd`` constructible only via an explicit kwarg -- see
        ``_WithoutCwdBinding``."""
        return (
            init_settings,
            _WithoutCwdBinding(env_settings),
            _WithoutCwdBinding(dotenv_settings),
            file_secret_settings,
        )


class CodexAdapter(ApprovalInterruptMixin, SimpleAdapter[CodexSessionState]):
    """
    Codex adapter backed by codex app-server (stdio or websocket transport).

    One Band room maps to one Codex thread. Mapping is persisted in task
    events metadata and restored via CodexHistoryConverter on bootstrap --
    so narrowing ``emit`` to exclude ``Emit.TASK_EVENTS`` doesn't just silence
    narration, it also stops thread-resume persistence: every restart starts
    a fresh Codex thread instead of resuming. Leave it in ``emit`` unless
    that's intended.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.TOOL_CALLS, Emit.THOUGHTS, Emit.TASK_EVENTS, Emit.USAGE}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )

    def __init__(
        self,
        config: CodexAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        history_converter: CodexHistoryConverter | None = None,
        client_factory: Callable[[CodexAdapterConfig], CodexClientProtocol]
        | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        self._config = config or CodexAdapterConfig()

        super().__init__(
            history_converter=history_converter or CodexHistoryConverter(),
            **features,
        )
        self.config = self._config
        self._custom_tools: list[CustomToolDef] = list(additional_tools or [])
        if self.config.enable_self_config_tools:
            self._custom_tools.extend(self._build_self_config_tools())
        if self.config.cwd is not None:
            raise ValueError(
                "cwd is not supported; use workspace_for_room or the default"
            )
        if self.config.transport != "stdio":
            raise ValueError(
                "only stdio Codex transport guarantees room process isolation"
            )
        if client_factory is not None:
            raise ValueError(
                "custom Codex clients cannot guarantee room process isolation"
            )
        self._room_clients: dict[str, RoomCodexClient] = {}
        # Final disposal gives a room's turn ``turn_settle_timeout_s`` to wind
        # down before cancelling it.
        self._retention = RoomRetention(
            self.config.retention, settle_grace_s=self.config.turn_settle_timeout_s
        )
        self._workspaces = RoomWorkspaces(self.config.workspace_for_room)
        self._active_room: ContextVar[str | None] = ContextVar(
            "codex_active_room", default=None
        )
        self._system_prompt: str = ""
        self._room_threads: dict[str, str] = {}
        self._prompt_injected_rooms: set[str] = set()
        self._room_task_titles: dict[str, OrderedDict[str, str]] = {}
        self._max_task_titles: int = _MAX_TASK_TITLES
        self._pending_approvals: dict[str, DecisionRegistry[PendingApproval]] = {}
        # A turn runs as a detached task so a manual approval can release
        # on_message: Band dispatches one message per room at a time, so the
        # reply that resolves the approval arrives only after on_message returns.
        self._turn_release: dict[str, asyncio.Future[None]] = {}
        self._turn_tasks: dict[str, TurnTask] = {}
        self._raw_history_by_room: dict[str, list[dict[str, Any]]] = {}
        self._needs_history_injection: set[str] = set()
        # Token usage tracking per thread
        self._token_usage: dict[str, CodexTokenUsage] = {}
        # Approval audit trail per room
        self._approval_audit: dict[str, list[ApprovalAuditEntry]] = {}
        # Session-level approved patterns.  OrderedDict (used as an ordered
        # set) so insertion order is preserved for LRU eviction when a room
        # hits ``max_session_approved_per_room``.  Value is unused.
        self._session_approved: dict[str, OrderedDict[str, None]] = {}
        # Per-room sandbox overrides (set via /sandbox command)
        self._sandbox_overrides: dict[str, str] = {}

    def _room_closing(self, room_id: str) -> bool:
        """Whether the room admits no more work; a forgotten room is closed."""
        room = self._room_clients.get(room_id)
        return room is None or room.residency.closing

    def _room_client(self, room_id: str) -> RoomCodexClient:
        room = self._room_clients.get(room_id)
        if room is not None:
            return room
        room = RoomCodexClient(
            room_id=room_id, workspace=self._workspaces.claim(room_id)
        )
        try:
            room.residency = self._retention.register(
                room_id,
                acquire=partial(self._acquire, room),
                release=partial(self._release, room),
                dispose=partial(self._dispose, room),
            )
        except BaseException:
            self._workspaces.release(room_id, room.workspace)
            raise
        self._room_clients[room_id] = room
        return room

    def _active_client_state(self) -> RoomCodexClient | None:
        room_id = self._active_room.get()
        return self._room_clients.get(room_id) if room_id is not None else None

    def _require_active_client_state(self) -> RoomCodexClient:
        state = self._active_client_state()
        if state is None:
            raise RuntimeError("Codex operation requires a room context")
        return state

    @property
    def _task_titles_by_id(self) -> OrderedDict[str, str]:
        room_id = self._active_room.get()
        if room_id is None:
            raise RuntimeError("Codex task state requires a room context")
        return self._room_task_titles.setdefault(room_id, OrderedDict())

    @property
    def _selected_model(self) -> str | None:
        state = self._active_client_state()
        if state is None:
            raise RuntimeError("Codex client requires a room context")
        return state.selected_model

    @_selected_model.setter
    def _selected_model(self, value: str | None) -> None:
        state = self._active_client_state()
        if state is None:
            raise RuntimeError("Codex client requires a room context")
        state.selected_model = value

    @property
    def _rpc_lock(self) -> asyncio.Lock:
        state = self._active_client_state()
        if state is None:
            raise RuntimeError("Codex client requires a room context")
        return state.rpc_lock

    def _build_self_config_tools(self) -> list[CustomToolDef]:
        """Build custom tools that let Codex change its own model/reasoning.

        Note: ``_handle_set_model`` and ``_handle_set_reasoning`` closures
        mutate adapter state (``config.model``, ``_selected_model``, etc.).
        They are safe because they are always called inside the
        ``_handle_server_request`` path which holds ``_rpc_lock``.
        """
        adapter = self

        def _handle_set_model(inp: SetModelInput) -> str:
            if not adapter._rpc_lock.locked():
                raise RuntimeError("_handle_set_model must run under _rpc_lock")
            adapter._require_active_client_state().model_override = inp.model
            adapter._selected_model = inp.model
            return f"Model changed to {inp.model} for subsequent turns."

        def _handle_set_reasoning(inp: SetReasoningInput) -> str:
            if not adapter._rpc_lock.locked():
                raise RuntimeError("_handle_set_reasoning must run under _rpc_lock")
            parts: list[str] = []
            if inp.effort is not None:
                # Unlike /reasoning, this closure is sync and holds _rpc_lock, so it
                # can't await a model/list round-trip to check the value live without
                # risking deadlock -- same reason _handle_set_model never validates
                # the model. An unsupported effort surfaces on the next turn instead.
                adapter._require_active_client_state().reasoning_effort = inp.effort
                parts.append(f"effort={inp.effort}")
            if inp.summary is not None:
                if inp.summary not in _REASONING_SUMMARIES:
                    return (
                        f"Invalid reasoning summary '{inp.summary}'. "
                        f"Valid: {', '.join(sorted(_REASONING_SUMMARIES))}."
                    )
                adapter._require_active_client_state().reasoning_summary = inp.summary
                parts.append(f"summary={inp.summary}")
            if not parts:
                return (
                    f"No changes. Current: effort={adapter.config.reasoning_effort or 'default'}, "
                    f"summary={adapter.config.reasoning_summary or 'default'}."
                )
            return f"Reasoning updated: {', '.join(parts)}."

        return [
            (SetModelInput, _handle_set_model),
            (SetReasoningInput, _handle_set_reasoning),
        ]

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        if self._retention.stopping:
            await self.cleanup_all()
        self._retention = self._retention.renewed()
        await super().on_started(agent_name, agent_description)
        self._build_system_prompt()
        self._log_startup_config(agent_name)

    def _log_startup_config(self, agent_name: str) -> None:
        # The two turn-task channels are independent by design but rendering
        # both produces duplicated timeline entries in most UIs.  Warn once
        # at startup so operators catch the misconfiguration instead of
        # discovering it as "twice as many events" downstream.
        if (
            self.config.emit_turn_task_markers
            and self.config.emit_turn_lifecycle_events
            and Emit.TASK_EVENTS in self.features.emit
        ):
            logger.warning(
                "Codex adapter: both emit_turn_task_markers and "
                "emit_turn_lifecycle_events are enabled — consumers will "
                "receive two task events per turn.  Pick exactly one channel "
                "(typically emit_turn_lifecycle_events for the richer metadata)."
            )
        logger.info(
            "Codex adapter started: agent=%s, transport=%s, model=%s, "
            "sandbox=%s, approval_mode=%s, "
            "execution_reporting=%s, self_config_tools=%s, "
            "task_events=%s, turn_markers=%s, thought_events=%s, "
            "stream_reasoning=%s, stream_plan=%s, stream_commentary=%s, "
            "diffs=%s, token_usage=%s",
            agent_name,
            self.config.transport,
            self.config.model or "auto",
            self.config.sandbox or "default",
            self.config.approval_mode,
            Emit.TOOL_CALLS in self.features.emit,
            self.config.enable_self_config_tools,
            Emit.TASK_EVENTS in self.features.emit,
            self.config.emit_turn_task_markers,
            Emit.THOUGHTS in self.features.emit,
            self.config.stream_reasoning_events,
            self.config.stream_plan_events,
            self.config.stream_commentary_events,
            self.config.emit_diff_events,
            self.config.emit_token_usage_events,
        )

    async def on_event(self, inp: AgentInput) -> None:
        if (
            self.config.inject_history_on_resume_failure
            and inp.is_session_bootstrap
            and inp.history.raw
        ):
            self._raw_history_by_room[inp.room_id] = inp.history.raw
        await super().on_event(inp)

    async def try_handle_control(
        self, msg: PlatformMessage, tools: AgentToolsProtocol
    ) -> bool:
        command = self._extract_local_command(msg.content)
        if command is None:
            return False
        room_id = msg.room_id
        if command[0] in _APPROVAL_COMMANDS:
            # Resolves a decision its turn already owns: no room or process.
            try:
                handled = await self._handle_approval_command(
                    tools=tools,
                    msg=msg,
                    room_id=room_id,
                    command=command[0],
                    args=command[1],
                )
            except DeliveryFailedError as e:
                reraise_delivery_cause(e)
            if handled:
                tools.turn.settle()
            return handled
        if command[0] != CodexCommand.STATUS:
            return False
        self._active_room.set(room_id)
        await self._handle_local_command(
            tools=tools,
            msg=msg,
            history=CodexSessionState(),
            room_id=room_id,
            command=command[0],
            args=command[1],
        )
        tools.turn.settle()
        return True

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: CodexSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        self._admit_turn(room_id, "Codex")
        room = self._room_client(room_id)
        self._active_room.set(room_id)
        command = self._extract_local_command(msg.content)

        release: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        turn = room.residency.start_owned(
            partial(
                self._run_turn,
                room=room,
                msg=msg,
                tools=tools,
                history=history,
                participants_msg=participants_msg,
                contacts_msg=contacts_msg,
                is_session_bootstrap=is_session_bootstrap,
                command=command,
            )
        )
        self._turn_release[room_id] = release
        self._turn_tasks[room_id] = bind_turn_task(tools, turn)
        turn.add_done_callback(lambda task: self._forget_turn(room_id, task))
        try:
            await asyncio.wait({release, turn}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            await self._cancel_turn(room_id)
            raise
        finally:
            if self._turn_release.get(room_id) is release:
                del self._turn_release[room_id]
        if turn.done():
            # No human was asked: the turn's outcome is on_message's own.
            await turn

    async def _run_turn(
        self,
        *,
        room: RoomCodexClient,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: CodexSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        is_session_bootstrap: bool,
        command: tuple[CodexCommand, str] | None,
    ) -> None:
        """Run one turn to completion, leasing its room's process throughout."""
        async with room.rpc_lock:
            try:
                if command is not None and await self._run_local_command(
                    room=room, msg=msg, tools=tools, history=history, command=command
                ):
                    return
                async with room.residency.use():
                    await self._serve_turn(
                        room=room,
                        msg=msg,
                        tools=tools,
                        history=history,
                        participants_msg=participants_msg,
                        contacts_msg=contacts_msg,
                        is_session_bootstrap=is_session_bootstrap,
                    )
            except DeliveryFailedError as e:
                reraise_delivery_cause(e)
            except (TurnResultAlreadyReported, InputNotAccepted):
                # A lease refused by a closing room is no failed turn: the
                # host keeps the message.
                raise
            except (CodexJsonRpcError, CodexContinuityError) as e:
                # A structured RPC error from the app-server (e.g. "model not
                # available") or a refused restore is safe, curated text --
                # unlike an arbitrary caught exception, worth showing verbatim.
                await tools.send_failure(AgentFailure(CODEX_PROVIDER, str(e)))
                raise
            except TimeoutError as timeout:
                # Only waiting on the room's process start or stop raises
                # this; an abandoned start stays owned until recovery closes it.
                logger.warning(
                    "Room %s: Codex process not ready", room.room_id, exc_info=timeout
                )
                await tools.send_failure(
                    AgentFailure(CODEX_PROVIDER, PROCESS_NOT_READY_ERROR)
                )
                raise
            except Exception:
                logger.exception(
                    "Unexpected error in Codex turn (room=%s)", room.room_id
                )
                await tools.send_failure(
                    AgentFailure(CODEX_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
                )
                raise

    async def _run_local_command(
        self,
        *,
        room: RoomCodexClient,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: CodexSessionState,
        command: tuple[CodexCommand, str],
    ) -> bool:
        """Handle a local command, leasing the room's process only for those
        that ask it: the rest must work while it cannot start, as
        ``/thread archive`` must after Codex refuses to restore the thread."""
        name, args = command
        lease = room.residency.use() if name in _PROCESS_COMMANDS else nullcontext()
        async with lease:
            handled = await self._handle_local_command(
                tools=tools,
                msg=msg,
                history=history,
                room_id=room.room_id,
                command=name,
                args=args,
            )
        if handled:
            tools.turn.settle()
        return handled

    async def _serve_turn(
        self,
        *,
        room: RoomCodexClient,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: CodexSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        is_session_bootstrap: bool,
    ) -> None:
        room_id = room.room_id
        thread_id = await self._ensure_thread(
            room=room,
            history=history,
            tools=tools,
            is_session_bootstrap=is_session_bootstrap,
        )
        turn_input, has_pending_prompt_injection = self._build_turn_input(
            msg=msg,
            participants_msg=participants_msg,
            contacts_msg=contacts_msg,
            room_id=room_id,
        )
        turn_params: dict[str, Any] = {"threadId": thread_id, "input": turn_input}
        self._apply_turn_overrides(turn_params, room_id=room_id)

        async with self._native_turn(room) as native:
            await self._start_native_turn(room, native, turn_params)
            if has_pending_prompt_injection:
                self._prompt_injected_rooms.add(room_id)
            await self._emit_turn_started(
                tools=tools,
                msg=msg,
                room_id=room_id,
                thread_id=thread_id,
                turn_id=native.turn_id,
            )

            usage_obj = self._token_usage.get(thread_id)
            if usage_obj is not None:
                usage_obj.reset_turn_deltas()

            # perf_counter (not monotonic): highest-resolution clock, so a fast
            # turn still measures a non-zero duration on Windows, where
            # monotonic()'s coarse tick can round an instant turn to 0.0.
            turn_start = _time.perf_counter()
            try:
                result = await self._process_turn_events(
                    native=native,
                    tools=tools,
                    msg=msg,
                    room_id=room_id,
                    thread_id=thread_id,
                    turn_start=turn_start,
                )
            except TurnResultAlreadyReported:
                # A nested handler (e.g. the turn-timeout branch) already
                # reported this failure via send_failure; propagate it
                # without emitting a friendly _emit_turn_outcome reply or
                # a generic "Internal error" fallback.
                raise
            except CodexJsonRpcError as error:
                await self._emit_failed_turn_outcome(
                    tools=tools,
                    msg=msg,
                    room_id=room_id,
                    thread_id=thread_id,
                    turn_id=native.turn_id,
                    result=TurnResult(turn_status="failed", turn_error=str(error)),
                    turn_start=turn_start,
                )
                raise
            except Exception:
                logger.exception(
                    "Unexpected error during Codex turn event processing "
                    "(thread=%s, turn=%s)",
                    thread_id,
                    native.turn_id,
                )
                await tools.send_failure(
                    AgentFailure(CODEX_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
                )
                await self._emit_failed_turn_outcome(
                    tools=tools,
                    msg=msg,
                    room_id=room_id,
                    thread_id=thread_id,
                    turn_id=native.turn_id,
                    result=TurnResult(
                        turn_status="failed",
                        turn_error="Internal error during turn processing",
                    ),
                    turn_start=turn_start,
                )
                raise TurnResultAlreadyReported(
                    "Internal error during turn processing"
                ) from None

        await self._emit_turn_outcome(
            tools=tools,
            msg=msg,
            room_id=room_id,
            thread_id=thread_id,
            turn_id=native.turn_id,
            turn_status=result.turn_status,
            turn_error=result.turn_error,
            native_messages=result.native_messages,
            duration_s=_time.perf_counter() - turn_start,
        )
        # A turn wound down by room cleanup was aborted, not missed.
        if not room.residency.closing:
            await judge_detached_turn(tools, room_id=room_id)

    async def _emit_turn_started(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        thread_id: str,
        turn_id: str | None,
    ) -> None:
        if Emit.TASK_EVENTS not in self.features.emit:
            return
        if self.config.emit_turn_task_markers:
            await send_event_safe(
                tools,
                content=self._build_task_event_content(
                    task_id=turn_id,
                    task="Codex turn",
                    status="started",
                    summary=f"Thread: {thread_id}",
                ),
                message_type="task",
                metadata={
                    "codex_thread_id": thread_id,
                    "codex_turn_id": turn_id,
                    "codex_room_id": room_id,
                },
                log_label="turn started task event",
                log_level=logging.DEBUG,
            )
        if self.config.emit_turn_lifecycle_events:
            await send_event_safe(
                tools,
                content=self._build_task_event_content(
                    task_id=turn_id,
                    task="Codex turn lifecycle",
                    status="started",
                    summary=f"Thread: {thread_id}",
                ),
                message_type="task",
                metadata={
                    "codex_event_type": "turn_lifecycle",
                    "codex_room_id": room_id,
                    "codex_thread_id": thread_id,
                    "codex_turn_id": turn_id,
                    "codex_turn_status": "started",
                    "codex_input_summary": (msg.content or "")[:200],
                },
                log_label="turn started lifecycle event",
                log_level=logging.DEBUG,
            )

    def _release_turn(self, room_id: str, tools: AgentToolsProtocol) -> None:
        """Let on_message return while the room's turn waits on a human; the
        turn is then judged at its real end, not when on_message returns."""
        release = self._turn_release.get(room_id)
        if release is not None and not release.done():
            tools.turn.detach()
            release.set_result(None)

    def _forget_turn(self, room_id: str, task: asyncio.Task[None]) -> None:
        owner = self._turn_tasks.get(room_id)
        if owner is not None and owner.task is task:
            del self._turn_tasks[room_id]

    async def _emit_failed_turn_outcome(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        thread_id: str,
        turn_id: str | None,
        result: TurnResult,
        turn_start: float,
    ) -> None:
        """Emit failure lifecycle events without posting a second reply."""
        await self._emit_turn_outcome(
            tools=tools,
            msg=msg,
            room_id=room_id,
            thread_id=thread_id,
            turn_id=turn_id,
            turn_status=result.turn_status,
            turn_error=result.turn_error,
            native_messages=result.native_messages,
            duration_s=_time.perf_counter() - turn_start,
            include_reply=False,
        )

    async def _process_turn_events(
        self,
        *,
        native: NativeTurn,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        thread_id: str,
        turn_start: float,
    ) -> TurnResult:
        """Consume the events addressed to one native turn and return its result."""
        turn_id = native.turn_id
        result = TurnResult()
        failure_reported = False
        try:
            while True:
                _remaining = max(
                    0.0,
                    self.config.turn_timeout_s - (_time.perf_counter() - turn_start),
                )
                event = await asyncio.wait_for(native.events.get(), timeout=_remaining)
                if isinstance(event, Exception):
                    raise event
                if event.kind == "request":
                    await self._answer_native_request(
                        native, tools=tools, msg=msg, room_id=room_id, event=event
                    )
                    continue

                params = _event_params(event)

                if event.method in {
                    "codex/event/task_started",
                    "codex/event/task_complete",
                }:
                    await self._forward_raw_task_event(
                        tools=tools,
                        room_id=room_id,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        method=event.method,
                        params=params,
                    )
                    continue

                if event.method == "error":
                    reported = await self._handle_error_event(
                        tools=tools,
                        params=params,
                        room_id=room_id,
                        thread_id=thread_id,
                        turn_id=turn_id,
                    )
                    failure_reported = failure_reported or reported
                    continue

                # --- Phase 3: Real-time streaming ---
                if event.method in {
                    "item/reasoning/summaryTextDelta",
                    "item/reasoning/textDelta",
                }:
                    if self.config.stream_reasoning_events:
                        delta = params.get("delta", "")
                        item_id = str(params.get("itemId") or "")
                        await send_event_safe(
                            tools,
                            content=str(delta),
                            message_type="thought",
                            metadata={
                                "streaming": True,
                                "codex_item_id": item_id,
                                "codex_event_type": event.method,
                                "codex_room_id": room_id,
                                "codex_thread_id": thread_id,
                                "codex_turn_id": turn_id,
                            },
                            log_label="reasoning delta",
                            log_level=logging.DEBUG,
                        )
                    continue

                if event.method == "item/plan/delta":
                    if self.config.stream_plan_events:
                        delta = params.get("delta", "")
                        item_id = str(params.get("itemId") or "")
                        await send_event_safe(
                            tools,
                            content=str(delta),
                            message_type="thought",
                            metadata={
                                "streaming": True,
                                "subtype": "plan",
                                "codex_item_id": item_id,
                                "codex_room_id": room_id,
                                "codex_thread_id": thread_id,
                                "codex_turn_id": turn_id,
                            },
                            log_label="plan delta",
                            log_level=logging.DEBUG,
                        )
                    continue

                # --- Phase 2: Plan step tracking ---
                if event.method == "turn/plan/updated":
                    if self.config.stream_plan_events:
                        await self._forward_plan_steps(
                            tools=tools,
                            params=params,
                            room_id=room_id,
                            thread_id=thread_id,
                            turn_id=turn_id,
                        )
                    continue

                # --- Phase 4: Token usage ---
                if event.method == "thread/tokenUsage/updated":
                    self._update_token_usage(thread_id, params)
                    if self.config.emit_token_usage_events:
                        await self._emit_token_usage_event(
                            tools=tools,
                            thread_id=thread_id,
                            room_id=room_id,
                        )
                    continue

                # --- Phase 2: Context compaction events ---
                if event.method == "context/compacted":
                    if (
                        self.config.emit_turn_lifecycle_events
                        and Emit.TASK_EVENTS in self.features.emit
                    ):
                        compacted_thread = str(params.get("threadId") or thread_id)
                        compacted_turn = str(params.get("turnId") or turn_id or "")
                        await send_event_safe(
                            tools,
                            content=self._build_task_event_content(
                                task_id=compacted_turn or None,
                                task="Codex context compaction",
                                status="completed",
                                summary=f"Thread: {compacted_thread}",
                            ),
                            message_type="task",
                            metadata={
                                "codex_event_type": "context_compaction",
                                "codex_room_id": room_id,
                                "codex_thread_id": compacted_thread,
                                "codex_turn_id": compacted_turn or None,
                            },
                            log_label="context compaction event",
                            log_level=logging.DEBUG,
                        )
                    continue

                # --- Phase 4: Aggregated diffs ---
                if event.method == "turn/diff/updated":
                    if (
                        self.config.emit_diff_events
                        and Emit.TASK_EVENTS in self.features.emit
                    ):
                        await self._forward_diff_event(
                            tools=tools,
                            params=params,
                            room_id=room_id,
                            thread_id=thread_id,
                            turn_id=turn_id,
                        )
                    continue

                if event.method == "item/started":
                    item = params.get("item")
                    if (
                        isinstance(item, dict)
                        and item.get("type") == CodexItemType.AGENT_MESSAGE
                    ):
                        result.native_messages[str(item["id"])] = NativeMessage(
                            phase=item.get("phase"),
                        )
                    continue

                if event.method == "item/agentMessage/delta":
                    item_id = str(params.get("itemId") or "")
                    message = result.native_messages.get(item_id)
                    delta = params.get("delta")
                    if (
                        message is not None
                        and message.phase == "commentary"
                        and isinstance(delta, str)
                        and self.config.stream_commentary_events
                        and Emit.THOUGHTS in self.features.emit
                    ):
                        delivered = await send_event_safe(
                            tools,
                            content=delta,
                            message_type="thought",
                            metadata={
                                "streaming": True,
                                "subtype": "commentary",
                                "codex_item_id": item_id,
                                "codex_room_id": room_id,
                                "codex_thread_id": thread_id,
                                "codex_turn_id": turn_id,
                            },
                            log_label="commentary delta",
                            log_level=logging.DEBUG,
                        )
                        if delivered:
                            message.streamed_text += delta
                    continue

                if event.method == "item/completed":
                    item = params.get("item")
                    if isinstance(item, dict):
                        if item.get("type") == CodexItemType.AGENT_MESSAGE:
                            message = result.native_messages.setdefault(
                                str(item["id"]),
                                NativeMessage(),
                            )
                            message.phase = item.get("phase")
                            text = item.get("text")
                            if isinstance(text, str):
                                message.completed_text = text
                        else:
                            await self._emit_item_completed_events(
                                tools=tools,
                                item=item,
                                room_id=room_id,
                                thread_id=thread_id,
                                turn_id=turn_id,
                            )
                    continue

                if event.method == TRANSPORT_CLOSED:
                    result.turn_status = "failed"
                    result.turn_error = TRANSPORT_LOST_ERROR
                    # Skipped when an earlier "error" notification in this same
                    # turn already reported one, so one incident isn't posted
                    # twice -- but the turn still fails either way.
                    if not failure_reported:
                        await tools.send_failure(
                            AgentFailure(
                                CODEX_PROVIDER, result.turn_error, "transport_closed"
                            )
                        )
                    raise TurnResultAlreadyReported(result.turn_error)

                if event.method == "turn/completed":
                    turn_payload = (
                        params.get("turn")
                        if isinstance(params.get("turn"), dict)
                        else {}
                    )
                    result.turn_status = str(turn_payload.get("status") or "failed")
                    result.turn_error = self._extract_turn_error(turn_payload)
                    if result.turn_status != "failed":
                        break
                    # Skipped when an earlier "error" notification in this same
                    # turn already reported one, so one incident isn't posted
                    # twice -- but the turn still fails either way.
                    if not failure_reported:
                        await self._emit_structured_turn_error(
                            tools=tools,
                            turn_payload=turn_payload,
                            room_id=room_id,
                            thread_id=thread_id,
                            turn_id=turn_id,
                        )
                    raise TurnResultAlreadyReported(result.turn_error or "Turn failed")
        except TurnResultAlreadyReported as error:
            result.turn_status = "failed"
            result.turn_error = str(error)
            await self._emit_failed_turn_outcome(
                tools=tools,
                msg=msg,
                room_id=room_id,
                thread_id=thread_id,
                turn_id=turn_id,
                result=result,
                turn_start=turn_start,
            )
            raise
        except TimeoutError:
            logger.error(
                "Codex turn timed out after %ss (thread=%s, turn=%s)",
                self.config.turn_timeout_s,
                thread_id,
                turn_id,
            )
            try:
                await native.client.request(
                    CodexRequestMethod.TURN_INTERRUPT,
                    {"threadId": thread_id, "turnId": turn_id},
                )
            except Exception:
                logger.warning(
                    "Failed to send turn/interrupt after timeout",
                    exc_info=True,
                )
            timeout_message = (
                f"Codex turn timed out after {self.config.turn_timeout_s}s"
            )
            await tools.send_failure(
                AgentFailure(CODEX_PROVIDER, timeout_message, FAILURE_CODE_TIMEOUT)
            )
            result.turn_status = "failed"
            result.turn_error = timeout_message
            # A raise from here lands in this try's own except clauses, not the
            # sibling `except TurnResultAlreadyReported` above -- Python never
            # matches an exception raised inside one except against a sibling
            # of the same try. Call the same failed-turn telemetry directly
            # (as on_message's CodexJsonRpcError branch already does) so a
            # timeout still gets the "Codex turn"/lifecycle failed-status
            # events every other TurnResultAlreadyReported path emits.
            await self._emit_failed_turn_outcome(
                tools=tools,
                msg=msg,
                room_id=room_id,
                thread_id=thread_id,
                turn_id=turn_id,
                result=result,
                turn_start=turn_start,
            )
            raise TurnResultAlreadyReported(timeout_message)
        return result

    async def _answer_native_request(
        self,
        native: NativeTurn,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        event: RpcEvent,
    ) -> None:
        """Answer the turn's request while its process can still read the
        answer; a lost process's requests go neither to the room nor to it."""
        if native.lost:
            return
        try:
            await self._handle_server_request(
                client=native.client,
                tools=tools,
                msg=msg,
                room_id=room_id,
                event=event,
            )
        except ConnectionError:
            if not native.lost:
                raise

    async def on_cleanup(self, room_id: str) -> None:
        """Leave a room: admission closes at once, its turn gets
        ``turn_settle_timeout_s`` to wind down, then its process closes and the
        room is forgotten.

        Unfinished or failed cleanup keeps the room registered, refusing work,
        with its process, thread and workspace claim for a retry to join.
        """
        room = self._room_clients.get(room_id)
        if room is None:
            return
        self._clear_pending_approvals_for_room(room_id)
        await room.residency.dispose()

    async def cleanup_all(self) -> None:
        """Stop the residency owner: admission closes at once, every room's
        turn gets ``turn_settle_timeout_s`` to wind down, then its process
        closes, all reported within one ``retention.transition_timeout_s``.

        Callers join the same stop, and a failed one is retried. An unfinished
        or failed stop keeps every room fenced with its process, thread and
        workspace. Success stays sealed until ``on_started``.
        """
        for room_id in list(self._room_clients):
            self._clear_pending_approvals_for_room(room_id)
        await self._retention.stop()

    async def _acquire(
        self, room: RoomCodexClient, generation: int, resume: bool
    ) -> None:
        """Start the room's process and strictly restore the thread it retains.

        A crash keeps the thread id, so ``resume`` changes nothing: a
        retained thread is always restored.
        The reader runs before ``thread/resume``, which can start native work.
        """
        room.client = client = self._build_client(room.workspace)
        await client.connect()
        room.reader = spawn_owned(
            self._read_native_events(room, client, generation),
            label=f"Room {room.room_id} Codex event reader",
        )
        await client.initialize(
            client_name=self.config.client_name,
            client_title=self.config.client_title,
            client_version=self.config.client_version,
            experimental_api=self.config.experimental_api,
        )
        await self._register_skill_roots(client)
        room.selected_model = await self._select_model(room, client)
        logger.info(
            "Room %s: Codex model selected: %s", room.room_id, room.selected_model
        )
        if (thread_id := self._room_threads.get(room.room_id)) is not None:
            await self._restore_thread(room, client, thread_id)

    def _resume_params(self, room: RoomCodexClient, thread_id: str) -> dict[str, Any]:
        """``thread/resume`` overrides pinning the thread to the room's workspace.

        Without ``cwd`` Codex reports the thread's recorded cwd, which a later
        strict restore would see as a changed setting.
        """
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": room.workspace,
            "personality": self.config.personality,
        }
        self._apply_thread_sandbox(params, room_id=room.room_id)
        return params

    async def _restore_thread(
        self, room: RoomCodexClient, client: CodexClientProtocol, thread_id: str
    ) -> None:
        """Resume exactly ``thread_id``; refusal keeps it for the next attempt.

        Sends only overrides ``thread/resume`` supports -- never ``path``,
        ``history`` or tools, which resume silently ignores -- and never
        starts a new thread in its place.
        """
        result = await client.request(
            CodexRequestMethod.THREAD_RESUME, self._resume_params(room, thread_id)
        )
        resumed = _result_id(result, "thread")
        if resumed != thread_id:
            raise CodexContinuityError(
                f"Codex resumed thread {resumed or 'none'!r} instead of {thread_id!r}"
            )
        if changes := room.settings.changes_in(ThreadSettings.reported_by(result)):
            raise CodexContinuityError(
                f"Codex restored thread {thread_id} with changed settings: "
                + "; ".join(changes)
            )
        room.restored = True

    async def _read_native_events(
        self, room: RoomCodexClient, client: CodexClientProtocol, generation: int
    ) -> None:
        """The process's one event reader, from connect until release.

        An event goes to the Band turn that started its native turn. Work no
        Band turn started -- a late item, an autonomous native turn -- is
        refused rather than handled with another turn's tools.
        """
        while True:
            try:
                event = await client.recv_event()
            except Exception as error:
                logger.exception(
                    "Room %s: Codex event reader failed; recovering generation %s",
                    room.room_id,
                    generation,
                )
                self._lose_process(room, generation, error)
                return
            if event.method == TRANSPORT_CLOSED:
                logger.warning(
                    "Room %s: Codex transport closed (%s); recovering generation %s",
                    room.room_id,
                    _loss_reason(event),
                    generation,
                )
                self._lose_process(room, generation, event)
                return
            if (
                unowned := self._route_native_event(room, generation, event)
            ) is not None:
                await self._refuse_unowned(room, client, unowned)

    @staticmethod
    def _route_native_event(
        room: RoomCodexClient, generation: int, event: RpcEvent
    ) -> RpcEvent | None:
        """Hand ``event`` to its Band turn; return it when no turn owns it."""
        native = room.turn
        if native is None or native.generation != generation:
            return event
        if native.turn_id is None:
            native.early.append(event)
            return None
        return None if native.claim(event) else event

    def _lose_process(
        self, room: RoomCodexClient, generation: int, cause: RpcEvent | Exception
    ) -> None:
        """Hand a process whose events stopped to recovery, and end the turn
        waiting on them with ``cause``: the transport's closing notice, or the
        reader's own failure. Recovery closes the process once that turn unwinds.
        """
        room.residency.recover(
            generation=generation,
            reason=f"Codex events stopped: {_loss_reason(cause)}",
        )
        native = room.turn
        if native is not None and native.generation == generation:
            native.lost = True
            # Approvals the lost process asked for can never be answered.
            self._clear_pending_approvals_for_room(room.room_id)
            native.events.put_nowait(cause)

    async def _refuse_unowned(
        self, room: RoomCodexClient, client: CodexClientProtocol, event: RpcEvent
    ) -> None:
        """Answer native work no Band turn started, without lending it one's tools."""
        if event.kind != "request" or event.id is None:
            logger.debug(
                "Room %s: no Band turn owns Codex %s; dropped",
                room.room_id,
                event.method,
            )
            return
        logger.warning(
            "Room %s: refusing Codex %s from native turn %s, which no Band turn started",
            room.room_id,
            event.method,
            _native_turn_id(event),
        )
        try:
            if event.method == DYNAMIC_TOOL_CALL_METHOD:
                await client.respond(
                    event.id, _tool_result(_UNOWNED_TOOL_CALL_TEXT, success=False)
                )
            elif event.method in CODEX_APPROVAL_METHODS:
                await client.respond(event.id, _approval_response("decline"))
            else:
                await _refuse_unhandled_request(
                    client, request_id=event.id, method=event.method
                )
        except Exception:
            logger.warning(
                "Room %s: could not refuse Codex %s",
                room.room_id,
                event.method,
                exc_info=True,
            )

    @asynccontextmanager
    async def _native_turn(self, room: RoomCodexClient) -> AsyncIterator[NativeTurn]:
        """Bind the native turn this Band turn starts to it until the turn ends.

        Whatever is still undelivered then belongs to no Band turn.
        """
        room.turn = native = NativeTurn(
            client=room.require_client(), generation=room.generation
        )
        try:
            yield native
        finally:
            room.turn = None
            await self._refuse_undelivered(room, native)

    async def _refuse_undelivered(
        self, room: RoomCodexClient, native: NativeTurn
    ) -> None:
        """Refuse a finished turn's leftovers, within the turn's settle bound so
        a process that stopped reading cannot hold the room's turn open."""
        if native.lost:
            # The process can never read a refusal.
            return
        try:
            async with asyncio.timeout(self.config.turn_settle_timeout_s):
                for event in _undelivered(native):
                    await self._refuse_unowned(room, native.client, event)
        except TimeoutError:
            logger.warning(
                "Room %s: Codex stopped reading; left native requests unanswered",
                room.room_id,
            )

    async def _start_native_turn(
        self, room: RoomCodexClient, native: NativeTurn, params: dict[str, Any]
    ) -> None:
        """Start the native turn and claim the events that arrived before its id."""
        native.thread_id = params["threadId"]
        started = await native.client.request(CodexRequestMethod.TURN_START, params)
        turn_id = _result_id(started, "turn")
        if not turn_id:
            raise RuntimeError("Codex turn/start returned no turn id")
        native.turn_id = turn_id
        early, native.early = native.early, []
        # Claim every early event before any await, or the reader could
        # queue newer ones ahead of them.
        refused = [event for event in early if not native.claim(event)]
        for event in refused:
            await self._refuse_unowned(room, native.client, event)

    async def _release(
        self, room: RoomCodexClient, cause: ReleaseCause
    ) -> ReleaseOutcome:
        """Close the room's process; its thread id stays for the next process.

        Every examined Codex shutdown cancels background terminals, hooks and
        child threads, so an idle release is refused before anything closes.
        """
        if cause == ReleaseCause.IDLE:
            return ReleaseOutcome.BUSY
        await room.stop_reader()
        if room.client is not None:
            timeout = self.config.client_close_timeout_s
            try:
                await asyncio.wait_for(room.client.close(), timeout=timeout)
            except TimeoutError as error:
                # This becomes the leave's error, so it names what overran.
                raise TimeoutError(
                    f"Codex process for room {room.room_id} did not close "
                    f"within {timeout}s"
                ) from error
        room.client = None
        room.selected_model = None
        room.restored = False
        return ReleaseOutcome.RELEASED

    async def _dispose(self, room: RoomCodexClient) -> None:
        """Erase what only a final leave ends: thread, settings, workspace claim."""
        room_id = room.room_id
        self._forget_room_thread(room_id)
        self._clear_pending_approvals_for_room(room_id)
        self._approval_audit.pop(room_id, None)
        self._session_approved.pop(room_id, None)
        self._sandbox_overrides.pop(room_id, None)
        self._room_task_titles.pop(room_id, None)
        self._workspaces.release(room_id, room.workspace)
        if self._room_clients.get(room_id) is room:
            del self._room_clients[room_id]

    def _forget_room_thread(self, room_id: str) -> str | None:
        """Drop the room's thread mapping and per-thread state; return the thread id."""
        thread_id = self._room_threads.pop(room_id, None)
        if thread_id is not None:
            self._token_usage.pop(thread_id, None)
        self._prompt_injected_rooms.discard(room_id)
        self._needs_history_injection.discard(room_id)
        self._raw_history_by_room.pop(room_id, None)
        return thread_id

    async def _register_skill_roots(self, client: CodexClientProtocol) -> None:
        roots = self.config.skill_roots
        if not roots:
            return
        try:
            await client.request(
                CodexRequestMethod.SKILLS_EXTRA_ROOTS_SET, {"extraRoots": roots}
            )
        except CodexJsonRpcError as exc:
            raise RuntimeError(f"Codex rejected skill_roots {roots}: {exc}") from exc

    def _build_client(self, workspace: str) -> CodexClientProtocol:
        return CodexStdioClient(
            command=self.config.codex_command,
            cwd=workspace,
            env=self.config.codex_env,
        )

    async def _select_model(
        self, room: RoomCodexClient, client: CodexClientProtocol
    ) -> str:
        if room.model_override:
            return room.model_override
        if self.config.model:
            return self.config.model

        try:
            result = await client.request(CodexRequestMethod.MODEL_LIST, {})
        except Exception:
            logger.warning(
                "model/list failed; using default Codex model",
                exc_info=True,
            )
            return OPENAI_MODEL

        visible_model_ids = self._visible_model_ids(result)
        if visible_model_ids:
            return visible_model_ids[0]
        logger.warning("model/list has no visible models; using default Codex model")
        return OPENAI_MODEL

    async def _ensure_thread(
        self,
        *,
        room: RoomCodexClient,
        history: CodexSessionState,
        tools: AgentToolsProtocol,
        is_session_bootstrap: bool,
    ) -> str:
        """The room's thread: the retained one, one Band history names, or new."""
        room_id = room.room_id
        if (thread_id := self._room_threads.get(room_id)) is not None:
            if room.restored:
                self._confirm_restored_tools(room, tools, thread_id)
            return thread_id
        client = room.require_client()
        if is_session_bootstrap and (named := history.thread_id):
            resumed = await self._resume_from_history(
                room=room, client=client, named=named, tools=tools
            )
            if resumed is not None:
                return resumed
        else:
            self._raw_history_by_room.pop(room_id, None)
        return await self._start_thread(room=room, client=client, tools=tools)

    def _confirm_restored_tools(
        self, room: RoomCodexClient, tools: AgentToolsProtocol, thread_id: str
    ) -> None:
        """Refuse a turn whose tools differ from the restored thread's.

        ``thread/resume`` restores the tools a thread was started with and
        ignores any others, so new ones would silently never reach the model.
        """
        if room.registration is not None:
            offered = _tools_by_name(self._build_dynamic_tools(tools))
            registered = _tools_by_name(room.registration)
            if offered != registered:
                changed = sorted(offered.keys() ^ registered.keys())
                raise CodexContinuityError(
                    f"Codex thread {thread_id} keeps the tools it was started "
                    f"with; this turn's tools differ ({', '.join(changed) or 'schemas'}). "
                    f"Send `/{CodexCommand.THREAD} {CodexSubcommand.ARCHIVE}` "
                    "to start a new thread."
                )
        room.restored = False

    async def _resume_from_history(
        self,
        *,
        room: RoomCodexClient,
        client: CodexClientProtocol,
        named: str,
        tools: AgentToolsProtocol,
    ) -> str | None:
        """Resume the thread Band history names; None falls back to a new one."""
        room_id = room.room_id
        try:
            result = await client.request(
                CodexRequestMethod.THREAD_RESUME,
                self._resume_params(room, named),
            )
        except CodexJsonRpcError as exc:
            logger.warning(
                "thread/resume failed for room %s thread %s: %s",
                room_id,
                named,
                exc,
            )
            if self.config.inject_history_on_resume_failure:
                self._needs_history_injection.add(room_id)
            return None
        thread_id = _result_id(result, "thread") or named
        self._room_threads[room_id] = thread_id
        # Band history does not record which tools the thread was started with.
        room.bind_thread(registration=None, settings=ThreadSettings.reported_by(result))
        self._raw_history_by_room.pop(room_id, None)
        if Emit.TASK_EVENTS in self.features.emit:
            await send_event_safe(
                tools,
                content=self._build_task_event_content(
                    task_id=thread_id,
                    task="Codex thread",
                    status="resumed",
                    summary=f"Room: {room_id}",
                ),
                message_type="task",
                metadata={
                    "codex_thread_id": thread_id,
                    "codex_room_id": room_id,
                    "codex_resumed": True,
                },
                log_label="thread resumed task event",
                log_level=logging.DEBUG,
            )
        return thread_id

    async def _start_thread(
        self,
        *,
        room: RoomCodexClient,
        client: CodexClientProtocol,
        tools: AgentToolsProtocol,
    ) -> str:
        room_id = room.room_id
        dynamic_tools = self._build_dynamic_tools(tools)
        start_params: dict[str, Any] = {
            "model": room.selected_model,
            "cwd": room.workspace,
            "approvalPolicy": self.config.approval_policy,
            "personality": self.config.personality,
            "dynamicTools": dynamic_tools,
        }
        self._apply_thread_sandbox(start_params, room_id=room_id)

        started = await client.request(CodexRequestMethod.THREAD_START, start_params)
        thread_id = _result_id(started, "thread")
        if not thread_id:
            raise RuntimeError("Codex thread/start returned no thread id")

        # Recorded at once: a process lost from here on restores this thread.
        self._room_threads[room_id] = thread_id
        room.bind_thread(
            registration=dynamic_tools, settings=ThreadSettings.reported_by(started)
        )

        if Emit.TASK_EVENTS in self.features.emit:
            await send_event_safe(
                tools,
                content=self._build_task_event_content(
                    task_id=thread_id,
                    task="Codex thread",
                    status="mapped",
                    summary=f"Transport: {self.config.transport}",
                ),
                message_type="task",
                metadata={
                    "codex_thread_id": thread_id,
                    "codex_room_id": room_id,
                    "codex_created_at": datetime.now(UTC).isoformat(),
                    "codex_transport": self.config.transport,
                },
                log_label="thread mapped task event",
                log_level=logging.DEBUG,
            )

        return thread_id

    def _build_dynamic_tools(self, tools: AgentToolsProtocol) -> list[dict[str, Any]]:
        dynamic_tools: list[dict[str, Any]] = []
        seen: set[str] = set()

        for schema in tools.get_openai_tool_schemas(
            capabilities=self.features.capabilities
        ):
            if not isinstance(schema, dict):
                continue

            if schema.get("type") == "function":
                function = schema.get("function") if isinstance(schema, dict) else {}
                if not isinstance(function, dict):
                    continue
                name = function.get("name")
                if not isinstance(name, str) or not name or name in seen:
                    continue
                dynamic_tools.append(
                    {
                        "name": name,
                        "description": str(function.get("description") or ""),
                        "inputSchema": function.get("parameters")
                        if isinstance(function.get("parameters"), dict)
                        else {"type": "object", "properties": {}},
                    }
                )
                seen.add(name)
                continue

            name = schema.get("name")
            input_schema = schema.get("inputSchema") or schema.get("input_schema")
            if not isinstance(name, str) or not name or name in seen:
                continue
            if not isinstance(input_schema, dict):
                input_schema = {"type": "object", "properties": {}}
            dynamic_tools.append(
                {
                    "name": name,
                    "description": str(schema.get("description") or ""),
                    "inputSchema": input_schema,
                }
            )
            seen.add(name)

        dynamic_tools = filter_tool_schemas(
            dynamic_tools,
            self.features,
            get_name=lambda schema: schema["name"],
            get_category=lambda schema: get_band_tool_category(schema["name"]),
        )
        for tool in self.config.additional_dynamic_tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not isinstance(name, str) or not name or name in seen:
                continue
            dynamic_tools.append(tool)
            seen.add(name)

        for model_cls, _ in self._custom_tools:
            schema = custom_tool_to_openai_schema(model_cls)
            function = schema.get("function", {})
            name = function.get("name", "")
            if not name or name in seen:
                continue
            dynamic_tools.append(
                {
                    "name": name,
                    "description": str(function.get("description") or ""),
                    "inputSchema": function.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
            seen.add(name)

        return dynamic_tools

    def _build_turn_input(
        self,
        *,
        msg: PlatformMessage,
        participants_msg: str | None,
        contacts_msg: str | None,
        room_id: str,
    ) -> tuple[list[dict[str, str]], bool]:
        items: list[dict[str, str]] = []
        injected_system_prompt = False

        if room_id not in self._prompt_injected_rooms and self._system_prompt:
            items.append(
                {
                    "type": "text",
                    "text": "[System Instructions]\n" + self._system_prompt,
                }
            )
            injected_system_prompt = True

        if room_id in self._needs_history_injection:
            self._needs_history_injection.discard(room_id)
            raw_history = self._raw_history_by_room.pop(room_id, None)
            if raw_history:
                context = self._format_history_context(raw_history)
                if context:
                    items.append({"type": "text", "text": context})

        if participants_msg:
            items.append({"type": "text", "text": f"[System]: {participants_msg}"})

        if contacts_msg:
            items.append({"type": "text", "text": f"[System]: {contacts_msg}"})

        items.append({"type": "text", "text": msg.format_for_llm()})
        return items, injected_system_prompt

    def _format_history_context(self, raw: list[dict[str, Any]]) -> str | None:
        text_messages = build_replay_messages(raw)
        if not text_messages:
            return None

        truncated = text_messages[-self.config.max_history_messages :]
        header = (
            "[Conversation History]\n"
            "The following is the conversation history from a previous session. "
            "Use it to maintain continuity.\n"
        )
        return header + "\n".join(truncated)

    async def _handle_server_request(
        self,
        *,
        client: CodexClientProtocol,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        event: RpcEvent,
    ) -> None:
        """Dispatch a request the Band turn's native turn sent (tool call, approval).

        ``client`` is the process the request came from; the answer goes back
        there. Concurrency model: this coroutine mutates room-scoped adapter
        state (``_pending_approvals``, ``_session_approved``,
        ``_approval_audit``, ``_task_titles_by_id``) without an explicit lock.
        It is safe because it runs only inside a turn, which holds its room's
        ``rpc_lock`` -- and every structure it touches is
        keyed (or routed via ``_active_room``) per room, so a concurrent
        turn in a different room never touches the same state.
        """
        if event.id is None:
            return

        params = _event_params(event)

        if event.method == DYNAMIC_TOOL_CALL_METHOD:
            tool_name = str(params.get("tool") or "")
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            call_id = str(params.get("callId") or "")

            # Don't emit reporting for codex-local slash commands — they already
            # surface their outcome in the room themselves.
            should_report = (
                Emit.TOOL_CALLS in self.features.emit
                and tool_name not in _SILENT_REPORTING_TOOLS
            )

            if should_report:
                await tools.send_event(
                    content=json.dumps(
                        {
                            ToolEventKey.NAME: tool_name,
                            ToolEventKey.ARGS: redact_tool_call_args(
                                tool_name, arguments
                            ),
                            ToolEventKey.TOOL_CALL_ID: call_id,
                        }
                    ),
                    message_type="tool_call",
                )

            try:
                custom_tool = find_custom_tool(self._custom_tools, tool_name)
                if custom_tool:
                    result = await execute_custom_tool(
                        custom_tool, arguments, turn=tools.turn
                    )
                    success = True
                else:
                    # Structured: a base tool (e.g. band_send_message) can fail without
                    # raising (bad args, API error) via ok=False; the plain variant would
                    # report that failure to Codex as success.
                    outcome = await tools.execute_tool_call_structured(
                        tool_name, arguments
                    )
                    result = outcome.value
                    success = outcome.ok
                if success and is_image_passthrough_result(tool_name, result):
                    content_items = _image_content_items(result)
                    text_result = image_block_placeholder(len(content_items))
                else:
                    text_result = (
                        result
                        if isinstance(result, str)
                        else json.dumps(result, default=str)
                    )
                    content_items = [_text_item(text_result)]
                await client.respond(
                    event.id, _tool_response(content_items, success=success)
                )
                if should_report:
                    await tools.send_event(
                        content=json.dumps(
                            {
                                ToolEventKey.NAME: tool_name,
                                ToolEventKey.OUTPUT: text_result,
                                ToolEventKey.TOOL_CALL_ID: call_id,
                            }
                        ),
                        message_type="tool_result",
                    )
            except ValidationError as exc:
                errors = format_validation_error(exc)
                error_text = f"Invalid arguments for {tool_name}: {errors}"
                logger.error("Validation error for tool %s: %s", tool_name, exc)
                await client.respond(event.id, _tool_result(error_text, success=False))
                if should_report:
                    await tools.send_event(
                        content=json.dumps(
                            {
                                ToolEventKey.NAME: tool_name,
                                ToolEventKey.OUTPUT: error_text,
                                ToolEventKey.TOOL_CALL_ID: call_id,
                            }
                        ),
                        message_type="tool_result",
                    )
            except Exception as exc:
                error_text = f"Error: {exc}"
                logger.exception("Tool execution failed for %s", tool_name)
                await client.respond(event.id, _tool_result(error_text, success=False))
                if should_report:
                    await tools.send_event(
                        content=json.dumps(
                            {
                                ToolEventKey.NAME: tool_name,
                                ToolEventKey.OUTPUT: error_text,
                                ToolEventKey.TOOL_CALL_ID: call_id,
                            }
                        ),
                        message_type="tool_result",
                    )

            return

        if event.method in CODEX_APPROVAL_METHODS:
            await self._handle_approval_request(
                client=client,
                tools=tools,
                msg=msg,
                room_id=room_id,
                event=event,
                params=params,
            )
            return

        await _refuse_unhandled_request(
            client, request_id=event.id, method=event.method
        )

    async def _handle_approval_request(
        self,
        *,
        client: CodexClientProtocol,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        event: RpcEvent,
        params: dict[str, Any],
    ) -> None:
        """Resolve a Codex approval request (command / file change).

        Splits off from :meth:`_handle_server_request` for readability.
        Same concurrency guarantees apply — see the docstring there.
        """
        if event.id is None:
            return

        summary = self._approval_summary(event.method, params)

        # Check session-level auto-approval first.
        session_key = self._session_approval_key(event.method, params)
        room_session = self._session_approved.get(room_id)
        session_hit = bool(session_key and room_session and session_key in room_session)

        if session_hit:
            decision: ApprovalDecision = "acceptForSession"
            decided_by = "session_policy"
        elif self._room_closing(room_id):
            decision = "decline"
            decided_by = "room_cleanup"
        elif self.config.approval_mode == "manual":
            try:
                decision = await self._resolve_manual_approval(
                    tools=tools,
                    msg=msg,
                    room_id=room_id,
                    event=event,
                    summary=summary,
                    params=params,
                )
                decided_by = msg.sender_name or msg.sender_type or "user"
            except Exception:
                # Ensure we still answer the server request even if the
                # human-facing notification flow fails.
                logger.exception("Manual approval flow failed; defaulting to decline")
                decision = "decline"
                decided_by = "system_fallback"
        else:
            decision = (
                "accept" if self.config.approval_mode == "auto_accept" else "decline"
            )
            decided_by = f"policy:{self.config.approval_mode}"

        await client.respond(event.id, _approval_response(decision))

        audit_entry = self._record_approval_audit(
            room_id=room_id,
            request_id=str(event.id),
            method=event.method,
            decision=decision,
            decided_by=decided_by,
            summary=summary,
            session_level=session_hit,
        )
        await self._emit_approval_audit_event(
            tools=tools,
            room_id=room_id,
            entry=audit_entry,
        )

        if (
            self.config.approval_mode != "manual"
            and self.config.approval_text_notifications
        ):
            mention = [
                {
                    "id": msg.sender_id,
                    "name": msg.sender_name or msg.sender_type,
                }
            ]
            try:
                await tools.send_notice(
                    f"Approval requested ({summary}). Policy decision: {decision}.",
                    mentions=mention,
                )
            except Exception:
                logger.exception("Failed to send approval policy notification")

        if Emit.THOUGHTS in self.features.emit:
            # Best-effort telemetry — never fail the turn on thought emission
            # failures.
            await send_event_safe(
                tools,
                content=(f"Codex approval request handled automatically ({decision})."),
                message_type="thought",
                metadata={
                    "codex_approval_method": event.method,
                    "codex_approval_type": self._approval_type(event.method),
                    "codex_approval_options": [
                        "accept",
                        "acceptForSession",
                        "decline",
                    ],
                },
                log_label="approval thought event",
                log_level=logging.DEBUG,
            )

    @staticmethod
    def _turn_usage(usage: CodexTokenUsage | None) -> TurnUsage:
        """Map codex's per-turn token deltas onto the framework-agnostic TurnUsage.

        Uses the per-turn deltas (rise from the turn-start anchor), not the
        thread cumulatives, so each emitted record is this turn's usage —
        matching what the in-process adapters emit. Codex reports no cache
        dimension, so those stay 0; a ``None`` usage yields empty (no emit).

        Codex reports reasoning tokens *disjointly* from output (its own total is
        ``input + output + reasoning``), so fold reasoning into ``output_tokens``
        — otherwise reasoning-heavy turns undercount, and this stays consistent
        with providers that already count reasoning inside output.
        """
        return TurnUsage.from_object(
            usage,
            input="turn_input_tokens",
            output="turn_output_tokens",
            reasoning="turn_reasoning_tokens",
        )

    async def _emit_turn_outcome(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        thread_id: str,
        turn_id: str | None,
        turn_status: str,
        turn_error: str,
        native_messages: dict[str, NativeMessage],
        duration_s: float = 0.0,
        include_reply: bool = True,
    ) -> None:
        # Look up token usage once for both marker and lifecycle events.
        usage = self._token_usage.get(thread_id)
        has_usage = usage is not None and usage.total_tokens > 0

        # Emit this turn's usage through the unified Emit.USAGE seam so the
        # toolkit's capture.usage() reads codex like every other adapter. This
        # is independent of the codex-specific emit_token_usage_events path
        # (which embeds codex_*_tokens on the marker/lifecycle task events);
        # emit_usage no-ops unless Emit.USAGE is enabled and usage is non-empty.
        await self.emit_usage(tools, self._turn_usage(usage))

        if (
            Emit.TASK_EVENTS in self.features.emit
            and self.config.emit_turn_task_markers
        ):
            summary = f"Thread: {thread_id}"
            if turn_error:
                summary += f" | Error: {turn_error}"
            metadata: dict[str, Any] = {
                "codex_room_id": room_id,
                "codex_thread_id": thread_id,
                "codex_turn_id": turn_id,
                "codex_turn_status": turn_status,
                "codex_error": turn_error or None,
            }
            if duration_s > 0:
                metadata["codex_duration_s"] = round(duration_s, 2)
            if has_usage:
                metadata.update(usage.to_metadata())
            await send_event_safe(
                tools,
                content=self._build_task_event_content(
                    task_id=turn_id,
                    task="Codex turn",
                    status=turn_status,
                    summary=summary,
                ),
                message_type="task",
                metadata=metadata,
                log_label="turn outcome task event",
                log_level=logging.DEBUG,
            )

        # Phase 2: Enriched turn lifecycle events
        if (
            self.config.emit_turn_lifecycle_events
            and Emit.TASK_EVENTS in self.features.emit
        ):
            lifecycle_metadata: dict[str, Any] = {
                "codex_event_type": "turn_lifecycle",
                "codex_room_id": room_id,
                "codex_thread_id": thread_id,
                "codex_turn_id": turn_id,
                "codex_turn_status": turn_status,
            }
            # Match the marker path: only include duration once it's been
            # measured.  Avoids a misleading "0.0s" on synthetic paths that
            # emit lifecycle events without timing (e.g. future code paths
            # that reuse _emit_turn_outcome without a real turn_start).
            if duration_s > 0:
                lifecycle_metadata["codex_duration_s"] = round(duration_s, 2)
            if turn_error:
                lifecycle_metadata["codex_error"] = turn_error
            if has_usage:
                lifecycle_metadata.update(usage.to_metadata())
            await send_event_safe(
                tools,
                content=self._build_task_event_content(
                    task_id=turn_id,
                    task="Codex turn lifecycle",
                    status=turn_status,
                    summary=f"Duration: {duration_s:.1f}s | Thread: {thread_id}",
                ),
                message_type="task",
                metadata=lifecycle_metadata,
                log_label="turn lifecycle event",
                log_level=logging.DEBUG,
            )

        if not include_reply:
            return

        mention = self._sender_mention(msg)

        if turn_status == "completed":
            if not tools.turn.replied and Emit.THOUGHTS in self.features.emit:
                for item_id, message in native_messages.items():
                    text = message.unstreamed_text
                    if not has_visible_content(text):
                        continue
                    await send_event_safe(
                        tools,
                        content=text,
                        message_type="thought",
                        metadata={
                            "codex_item_id": item_id,
                            "codex_room_id": room_id,
                            "codex_thread_id": thread_id,
                            "codex_turn_id": turn_id,
                        },
                        log_label="Codex completed thought",
                    )
            return

        if turn_status == "interrupted":
            outcome_text = "I stopped before completing this request."
        elif turn_error:
            outcome_text = (
                f"I couldn't complete this request ({turn_status}): {turn_error}"
            )
        else:
            outcome_text = f"I couldn't complete this request ({turn_status})."
        await deliver_notice(tools, outcome_text, mentions=mention)
        tools.turn.settle()

    async def _emit_item_completed_events(
        self,
        *,
        tools: AgentToolsProtocol,
        item: dict[str, Any],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
    ) -> None:
        """Forward internal Codex operations as platform events.

        Best-effort: failures are logged but never propagated, since
        execution reporting is supplementary and must not kill message
        processing (e.g. a transient 403 from the load balancer).
        """
        item_type = item.get("type", "")
        item_id = str(
            item.get("id") or item.get("callId") or item.get("toolCallId") or ""
        )
        metadata = {
            "codex_room_id": room_id,
            "codex_thread_id": thread_id,
            "codex_turn_id": turn_id,
        }

        try:
            await self._emit_item_event(
                tools=tools,
                item_type=item_type,
                item_id=item_id,
                item=item,
                metadata=metadata,
            )
        except Exception:
            # Best-effort telemetry — never fail the turn on emit failures.
            logger.debug(
                "Failed to emit %s event for item %s (best-effort)",
                item_type,
                item_id,
                exc_info=True,
            )

    async def _emit_item_event(
        self,
        *,
        tools: AgentToolsProtocol,
        item_type: str,
        item_id: str,
        item: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        """Inner dispatch for item events — may raise on API errors."""
        # Tool-like items gated on Emit.TOOL_CALLS
        if item_type in _TOOL_ITEM_TYPES:
            if Emit.TOOL_CALLS not in self.features.emit:
                return
            name, args, output = self._extract_tool_item(item_type, item)
            await tools.send_event(
                content=json.dumps(
                    {
                        ToolEventKey.NAME: name,
                        ToolEventKey.ARGS: args,
                        ToolEventKey.TOOL_CALL_ID: item_id,
                    }
                ),
                message_type="tool_call",
                metadata=metadata,
            )
            await tools.send_event(
                content=json.dumps(
                    {
                        ToolEventKey.NAME: name,
                        ToolEventKey.OUTPUT: output,
                        ToolEventKey.TOOL_CALL_ID: item_id,
                    }
                ),
                message_type="tool_result",
                metadata=metadata,
            )
            return

        # Thought-like items gated on Emit.THOUGHTS
        if item_type in _THOUGHT_ITEM_TYPES:
            if Emit.THOUGHTS not in self.features.emit:
                return
            text = self._extract_thought_text(item_type, item)
            if not text:
                # Empty reasoning/plan items carry no information — skip rather
                # than posting a placeholder like "(reasoning)" / "(plan)".
                return
            await tools.send_event(
                content=text,
                message_type="thought",
                metadata=metadata,
            )
            return

        if item_type in _UNRENDERED_ITEM_TYPES:
            return

        logger.debug("Unhandled item/completed type: %s", item_type)

    @staticmethod
    def _extract_tool_item(item_type: str, item: dict[str, Any]) -> CodexToolItem:
        """Extract (name, args, output) for a tool-like item."""
        match item_type:
            case CodexItemType.COMMAND_EXECUTION:
                return CodexAdapter._extract_command_execution(item)
            case CodexItemType.FILE_CHANGE:
                return CodexAdapter._extract_file_change(item)
            case CodexItemType.MCP_TOOL_CALL:
                return CodexAdapter._extract_mcp_tool_call(item)
            case CodexItemType.WEB_SEARCH:
                return CodexAdapter._extract_web_search(item)
            case CodexItemType.IMAGE_VIEW:
                return CodexAdapter._extract_image_view(item)
            case CodexItemType.COLLAB_AGENT_TOOL_CALL:
                return CodexAdapter._extract_collab_agent_tool_call(item)
            case _:
                return CodexToolItem(item_type, {}, "completed")

    @staticmethod
    def _extract_command_execution(item: dict[str, Any]) -> CodexToolItem:
        command = item.get("command", "")
        cwd = item.get("cwd", "")
        args: dict[str, Any] = {"command": command, "cwd": cwd}
        output_parts: list[str] = []
        if item.get("aggregated_output"):
            output_parts.append(str(item["aggregated_output"]))
        exit_code = item.get("exitCode")
        if exit_code is not None:
            output_parts.append(f"exit_code={exit_code}")
        status = item.get("status", "")
        output = "\n".join(output_parts) if output_parts else str(status)
        return CodexToolItem("exec", args, output)

    @staticmethod
    def _extract_file_change(item: dict[str, Any]) -> CodexToolItem:
        changes = item.get("changes", [])
        if not isinstance(changes, list):
            changes = []
        file_paths = [c.get("path", "") for c in changes if isinstance(c, dict)]
        return CodexToolItem(
            "file_edit",
            {"files": file_paths},
            str(item.get("status", "applied")),
        )

    @staticmethod
    def _extract_mcp_tool_call(item: dict[str, Any]) -> CodexToolItem:
        server = item.get("server", "")
        tool = item.get("tool", "")
        name = f"mcp:{server}/{tool}"
        mcp_args = item.get("arguments", {})
        if not isinstance(mcp_args, dict):
            mcp_args = {}
        # redact_tool_call_args compares against the bare tool name (e.g.
        # "band_send_room_file"), not the "mcp:{server}/{tool}" display name
        # this method returns -- redact before that prefix is applied.
        mcp_args = redact_tool_call_args(tool, mcp_args)
        output = CodexAdapter._stringify_tool_output(
            item.get("result"),
            item.get("error"),
            default="completed",
            raw_fallback=True,
        )
        return CodexToolItem(name, mcp_args, output)

    @staticmethod
    def _extract_web_search(item: dict[str, Any]) -> CodexToolItem:
        query = item.get("query", "")
        output = CodexAdapter._stringify_tool_output(
            item.get("action"), default="completed", raw_fallback=True
        )
        return CodexToolItem("web_search", {"query": query}, output)

    @staticmethod
    def _extract_image_view(item: dict[str, Any]) -> CodexToolItem:
        path = item.get("path", "")
        return CodexToolItem(
            "view_image",
            {"path": path},
            str(item.get("status", "viewed")),
        )

    @staticmethod
    def _extract_collab_agent_tool_call(item: dict[str, Any]) -> CodexToolItem:
        collab_tool = item.get("tool", "")
        name = f"collab:{collab_tool}"
        collab_args: dict[str, Any] = {}
        if item.get("prompt"):
            collab_args["prompt"] = item["prompt"]
        if item.get("agents"):
            collab_args["agents"] = item["agents"]
        output = CodexAdapter._stringify_tool_output(
            item.get("result"), default="completed", raw_fallback=True
        )
        return CodexToolItem(name, collab_args, output)

    @staticmethod
    def _stringify_tool_output(
        *values: Any, default: str, raw_fallback: bool = False
    ) -> str:
        """Return the first present value as displayable text.

        Shared by tool-result narration and thought extraction: strings pass
        through; lists contribute joined ``str`` / ``dict["text"]`` entries.

        A list with nothing extractable has two different honest readings
        depending on the caller:

        - Thought extraction (``raw_fallback=False``, the default): the list is
          treated as carrying no information, so it is skipped in favor of the
          next candidate value (or ``default``) rather than becoming a
          placeholder like ``"[]"``.
        - Real tool output (``raw_fallback=True``): a non-text list (e.g. an
          MCP image/resource content block) is still real data, so it is
          dumped as JSON instead of being discarded.
        """

        def list_item_text(item: Any) -> str | None:
            if isinstance(item, str):
                return item
            if isinstance(item, dict):
                text = item.get("text")
                return text if isinstance(text, str) else None
            return None

        for value in values:
            if value is None:
                continue
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                text_parts = [
                    text for item in value if (text := list_item_text(item)) is not None
                ]
                if text_parts:
                    return "\n".join(text_parts)
                if raw_fallback:
                    return json.dumps(value, default=str)
                continue
            return json.dumps(value, default=str)
        return default

    @staticmethod
    def _extract_thought_text(item_type: str, item: dict[str, Any]) -> str | None:
        """Extract display text for a thought-like item.

        Reasoning summaries and plan text go through
        :meth:`_stringify_tool_output` (single source of truth for turning
        protocol payloads into display text). Returns ``None`` when nothing
        informative is present so callers can skip emission instead of posting
        a placeholder.
        """
        match item_type:
            case CodexItemType.REASONING:
                text = CodexAdapter._stringify_tool_output(
                    item.get("summary"), default=""
                ).strip()
                return text or None

            case CodexItemType.PLAN:
                text = CodexAdapter._stringify_tool_output(
                    item.get("text"), default=""
                ).strip()
                return text or None

            case CodexItemType.CONTEXT_COMPACTION:
                return "Context compaction performed"

            case CodexItemType.ENTERED_REVIEW_MODE | CodexItemType.EXITED_REVIEW_MODE:
                text = CodexAdapter._stringify_tool_output(
                    item.get("text"), default=""
                ).strip()
                return text or f"Review mode: {item_type}"

            case _:
                return None

    async def _resolve_manual_approval(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        event: RpcEvent,
        summary: str,
        params: dict[str, Any],
    ) -> ApprovalDecision:
        mention = self._sender_mention(msg)
        if event.id is None:
            raise RuntimeError("approval request must have an id")
        token = self._approval_token(event.id, params)
        loop = asyncio.get_running_loop()
        pending = PendingApproval(
            request_id=event.id,
            method=event.method,
            summary=summary,
            created_at=datetime.now(UTC),
            future=loop.create_future(),
            session_key=self._session_approval_key(event.method, params),
        )
        registry = self._pending_approvals.setdefault(
            room_id,
            DecisionRegistry(max_pending=self.config.max_pending_approvals_per_room),
        )
        if (registration := registry.register_keyed(pending, key=token)) is None:
            logger.warning(
                "Approval %s in room %s was redelivered after a reply claimed it; "
                "declining the redelivery",
                token,
                room_id,
            )
            return "decline"
        entry = registration.entry
        for removed in registration.removed:
            removed.payload.future.set_result("decline")
        if (evicted := registration.evicted) is not None:
            logger.warning(
                "Evicted oldest pending approval %s in room %s (limit %s)",
                evicted.token,
                room_id,
                self.config.max_pending_approvals_per_room,
            )
        try:
            approval_msg = APPROVAL_REQUESTED_TEMPLATE.format(
                summary=summary, token=token
            )
            # Emit enriched metadata as a task event for UI rendering
            if Emit.TASK_EVENTS in self.features.emit:
                approval_metadata: dict[str, Any] = {
                    "codex_event_type": "approval_request",
                    "codex_approval_type": self._approval_type(event.method),
                    "codex_approval_method": event.method,
                    "codex_room_id": room_id,
                    "codex_approval_options": [
                        "accept",
                        "acceptForSession",
                        "decline",
                    ],
                }
                if params.get("command"):
                    approval_metadata["codex_command"] = params["command"]
                if params.get("cwd"):
                    approval_metadata["codex_cwd"] = params["cwd"]
                if params.get("reason"):
                    approval_metadata["codex_reason"] = params["reason"]
                # Network context (domains, IPs) when available
                net_ctx = params.get("networkContext") or params.get("network_context")
                if net_ctx:
                    approval_metadata["codex_network_context"] = net_ctx
                await send_event_safe(
                    tools,
                    content=self._build_task_event_content(
                        task_id=token,
                        task="Codex approval request",
                        status="pending",
                        summary=summary,
                    ),
                    message_type="task",
                    metadata=approval_metadata,
                    log_label="approval request task event",
                    log_level=logging.DEBUG,
                )
            try:
                await tools.send_notice(approval_msg, mentions=mention)
            except Exception:
                logger.exception(
                    "Failed to notify room %s about pending approval %s",
                    room_id,
                    token,
                )
                # A reply that claimed it meanwhile (its id is in the task
                # event above) owns the answer; wait for it below.
                if registry.withdraw(entry):
                    # The room was never notified, so waiting out the full
                    # approval_wait_timeout_s would misreport a Band delivery
                    # hiccup as a human-decision timeout. Re-raise (rather
                    # than returning "decline") so the caller attributes it
                    # to "system_fallback" instead of the human sender.
                    await tools.send_failure(
                        AgentFailure(
                            CODEX_PROVIDER,
                            "Failed to notify the room about a pending approval "
                            "request; defaulting to decline.",
                        )
                    )
                    raise

            self._release_turn(room_id, tools)
            decision = await registry.wait(
                entry, pending.future, timeout_s=self.config.approval_wait_timeout_s
            )
            if decision is not Timeout.TIMED_OUT:
                return decision
            timeout_decision = self.config.approval_timeout_decision
            try:
                await tools.send_notice(
                    APPROVAL_TIMED_OUT_TEMPLATE.format(
                        token=token, decision=timeout_decision
                    ),
                    mentions=mention,
                )
            except Exception:
                logger.exception("Failed to send approval timeout notification")
            return timeout_decision
        finally:
            self._clear_pending_approval(room_id, entry)

    async def _forward_raw_task_event(
        self,
        *,
        tools: AgentToolsProtocol,
        room_id: str,
        thread_id: str,
        turn_id: str | None,
        method: str,
        params: dict[str, Any],
    ) -> None:
        if Emit.TASK_EVENTS not in self.features.emit:
            return

        is_started = method == "codex/event/task_started"
        task_phase = "started" if is_started else "completed"
        task_id = self._task_event_id(params)
        title = self._task_event_title(params)
        if task_id and title and is_started:
            self._task_titles_by_id[task_id] = title
            if len(self._task_titles_by_id) > self._max_task_titles:
                self._task_titles_by_id.popitem(last=False)
        if task_id and not title:
            title = self._task_titles_by_id.get(task_id)
        summary = self._task_event_summary(params)
        if not title:
            title = "Codex task lifecycle event"
            if not summary:
                summary = f"Method: {method}"
        content = self._build_task_event_content(
            task_id=task_id,
            task=title,
            status=task_phase,
            summary=summary,
        )

        metadata: dict[str, Any] = {
            "codex_room_id": room_id,
            "codex_thread_id": thread_id,
            "codex_turn_id": turn_id,
            "codex_event_method": method,
            "codex_task_phase": task_phase,
        }
        if task_id:
            metadata["codex_task_id"] = task_id
        if params:
            metadata["codex_event_params"] = params

        await tools.send_event(
            content=content,
            message_type="task",
            metadata=metadata,
        )
        if not is_started and task_id:
            self._task_titles_by_id.pop(task_id, None)

    # ------------------------------------------------------------------
    # Phase 1: Structured error handling
    # ------------------------------------------------------------------

    async def _handle_error_event(
        self,
        *,
        tools: AgentToolsProtocol,
        params: dict[str, Any],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
    ) -> bool:
        """Handle an ``error`` notification from Codex.

        Returns whether a failure was actually reported, so the turn loop can
        skip a redundant second report if ``turn/completed`` also arrives with
        a failed status for the same incident.
        """
        error_obj = params.get("error") or {}
        if isinstance(error_obj, dict):
            error_msg = error_obj.get("message", "")
        else:
            error_msg = str(error_obj)
            error_obj = {"message": error_msg}
        will_retry = bool(params.get("willRetry", False))
        if will_retry:
            # Include retry metadata so a retry storm is observable in logs
            # even though we don't surface transient errors to the user.
            retry_attempt = params.get("retryAttempt") or params.get("attempt")
            retry_after_s = params.get("retryAfterSeconds") or params.get("retryAfter")
            logger.warning(
                "Codex transient error (will retry, attempt=%s, retry_after=%ss, "
                "thread=%s, turn=%s): %s",
                retry_attempt,
                retry_after_s,
                thread_id,
                turn_id,
                error_msg,
            )
            return False

        logger.error("Codex error: %s", error_msg)
        await tools.send_failure(
            build_agent_failure(
                error_obj, thread_id=thread_id, turn_id=turn_id, room_id=room_id
            )
        )
        return True

    async def _emit_structured_turn_error(
        self,
        *,
        tools: AgentToolsProtocol,
        turn_payload: dict[str, Any],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
    ) -> None:
        """Emit a structured error event when turn/completed reports failure."""
        error = turn_payload.get("error")
        if not isinstance(error, dict):
            # A falsy scalar (``""``, ``0``, ``None``) has no useful message to
            # carry; build_agent_failure's own fallback covers it uniformly
            # instead of shipping a degenerate literal string like "None".
            error = {"message": str(error)} if error else {}
        logger.error(
            "Codex turn failed (thread=%s, turn=%s): %s",
            thread_id,
            turn_id,
            error.get("message", ""),
        )
        await tools.send_failure(
            build_agent_failure(
                error, thread_id=thread_id, turn_id=turn_id, room_id=room_id
            )
        )

    # ------------------------------------------------------------------
    # Phase 2: Plan step tracking
    # ------------------------------------------------------------------

    async def _forward_plan_steps(
        self,
        *,
        tools: AgentToolsProtocol,
        params: dict[str, Any],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
    ) -> None:
        """Forward turn/plan/updated with structured step-level status."""
        steps = parse_plan_steps(params)
        if not steps:
            return
        step_dicts = [{"step": s.step, "status": s.status} for s in steps]
        await send_event_safe(
            tools,
            content=self._build_task_event_content(
                task_id=turn_id,
                task="Codex plan",
                status="updated",
                summary=f"{len(steps)} steps",
            ),
            message_type="task",
            metadata={
                "codex_plan_steps": step_dicts,
                "codex_room_id": room_id,
                "codex_thread_id": thread_id,
                "codex_turn_id": turn_id,
            },
            log_label="plan steps",
            log_level=logging.DEBUG,
        )

    # ------------------------------------------------------------------
    # Phase 4: Token usage & diffs
    # ------------------------------------------------------------------

    def _update_token_usage(self, thread_id: str, params: dict[str, Any]) -> None:
        """Update accumulated token usage for a thread."""
        usage = self._token_usage.get(thread_id)
        if usage is None:
            usage = CodexTokenUsage()
            self._token_usage[thread_id] = usage
        usage.update(params)

    async def _emit_token_usage_event(
        self,
        *,
        tools: AgentToolsProtocol,
        thread_id: str,
        room_id: str,
    ) -> None:
        """Emit a task event with current token usage."""
        usage = self._token_usage.get(thread_id)
        # Dataclass instances are always truthy, so check for None and the
        # zero-tokens sentinel explicitly — otherwise we'd emit an empty event
        # before any thread/tokenUsage/updated notification has arrived.
        if usage is None or usage.total_tokens == 0:
            return
        metadata = usage.to_metadata()
        metadata["codex_thread_id"] = thread_id
        metadata["codex_room_id"] = room_id
        await send_event_safe(
            tools,
            content=usage.format_summary(),
            message_type="task",
            metadata=metadata,
            log_label="token usage event",
            log_level=logging.DEBUG,
        )

    async def _forward_diff_event(
        self,
        *,
        tools: AgentToolsProtocol,
        params: dict[str, Any],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
    ) -> None:
        """Forward turn/diff/updated as a task event.

        Diffs are semantically separate from tool results, so we emit a task
        event with ``codex_event_type=turn_diff`` and carry the raw diff in
        metadata.  Consumers filter on ``codex_event_type`` rather than the
        generic ``tool_result`` channel.
        """
        diff_content = str(params.get("diff") or params.get("content") or "")
        files_changed: list[str] = []
        files_raw = params.get("files") or params.get("filesChanged") or []
        if isinstance(files_raw, list):
            files_changed = [str(f) for f in files_raw if f]
        summary = (
            f"{len(files_changed)} files changed" if files_changed else "diff updated"
        )

        # Enforce the cap on UTF-8 bytes (not Python characters) so a diff
        # loaded with emoji or CJK can't silently inflate the WebSocket
        # frame well past the intended budget.
        diff_truncated = False
        diff_bytes = diff_content.encode("utf-8")
        original_byte_length = len(diff_bytes)
        original_length = len(diff_content)
        if original_byte_length > _MAX_DIFF_METADATA_BYTES:
            truncated_bytes = diff_bytes[:_MAX_DIFF_METADATA_BYTES]
            # Drop any trailing partial UTF-8 sequence introduced by slicing
            # inside a multi-byte character.
            diff_content = truncated_bytes.decode("utf-8", errors="ignore") + (
                f"\n... [truncated, {original_byte_length - _MAX_DIFF_METADATA_BYTES} "
                f"more bytes]"
            )
            diff_truncated = True

        metadata: dict[str, Any] = {
            "codex_event_type": "turn_diff",
            "codex_thread_id": thread_id,
            "codex_turn_id": turn_id,
            "codex_room_id": room_id,
            "codex_files_changed": files_changed,
            "codex_diff": diff_content,
        }
        if diff_truncated:
            metadata["codex_diff_truncated"] = True
            metadata["codex_diff_original_length"] = original_length
            metadata["codex_diff_original_bytes"] = original_byte_length
        await send_event_safe(
            tools,
            content=self._build_task_event_content(
                task_id=turn_id,
                task="Codex diff",
                status="updated",
                summary=summary,
            ),
            message_type="task",
            metadata=metadata,
            log_label="diff event",
            log_level=logging.DEBUG,
        )

    # ------------------------------------------------------------------
    # Phase 1: Approval audit trail
    # ------------------------------------------------------------------

    def _record_session_approval(self, room_id: str, key: str) -> None:
        """Register ``key`` as a session-level auto-approval for ``room_id``.

        Maintains insertion order so the oldest entry is evicted when the
        room exceeds ``max_session_approved_per_room``.  Moving an existing
        key to the most-recent position on re-approval keeps frequently
        re-approved commands from being evicted.
        """
        room = self._session_approved.setdefault(room_id, OrderedDict())
        if key in room:
            room.move_to_end(key)
        else:
            room[key] = None
        cap = self.config.max_session_approved_per_room
        while len(room) > cap:
            room.popitem(last=False)

    def _record_approval_audit(
        self,
        *,
        room_id: str,
        request_id: str,
        method: str,
        decision: str,
        decided_by: str,
        summary: str = "",
        session_level: bool = False,
    ) -> ApprovalAuditEntry:
        """Record an approval decision and return the stored entry."""
        entry = ApprovalAuditEntry(
            request_id=str(request_id),
            method=method,
            decision=decision,
            decided_by=decided_by,
            timestamp=datetime.now(UTC).isoformat(),
            summary=summary,
            session_level=session_level,
        )
        audit_list = self._approval_audit.setdefault(room_id, [])
        audit_list.append(entry)
        # Cap the audit trail to avoid unbounded memory growth in long sessions.
        if len(audit_list) > self.config.max_approval_audit_per_room:
            del audit_list[: len(audit_list) - self.config.max_approval_audit_per_room]
        return entry

    async def _emit_approval_audit_event(
        self,
        *,
        tools: AgentToolsProtocol,
        room_id: str,
        entry: ApprovalAuditEntry,
    ) -> None:
        """Emit a task event for an approval decision."""
        if Emit.TASK_EVENTS not in self.features.emit:
            return
        await send_event_safe(
            tools,
            content=self._build_task_event_content(
                task_id=str(entry.request_id),
                task="Codex approval",
                status=entry.decision,
                summary=entry.summary,
            ),
            message_type="task",
            metadata={
                "codex_event_type": "approval_resolution",
                "codex_approval_method": entry.method,
                "codex_approval_decision": entry.decision,
                "codex_decided_by": entry.decided_by,
                "codex_session_level": entry.session_level,
                "codex_room_id": room_id,
                "codex_timestamp": entry.timestamp,
            },
            log_label="approval audit event",
            log_level=logging.DEBUG,
        )

    async def _handle_local_command(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        history: CodexSessionState,
        room_id: str,
        command: CodexCommand,
        args: str,
    ) -> bool:
        mention = self._sender_mention(msg)

        if command == CodexCommand.HELP:
            await deliver_notice(tools, _HELP_TEXT, mentions=mention)
            return True

        if command == CodexCommand.STATUS:
            mapped_thread = self._room_threads.get(room_id) or history.thread_id or None
            usage = (
                self._token_usage.get(mapped_thread or "") if mapped_thread else None
            )
            usage_line = (
                usage.format_summary() if usage and usage.total_tokens > 0 else "none"
            )
            session_approvals = len(self._session_approved.get(room_id) or ())
            room = self._active_client_state()
            selected_model = room.selected_model if room is not None else None
            status_text = (
                "Codex status:\n"
                f"- transport: {self.config.transport}\n"
                f"- selected_model: {selected_model or 'unknown'}\n"
                f"- configured_model: {self.config.model or 'auto'}\n"
                f"- room_id: {room_id}\n"
                f"- thread_id: {mapped_thread or 'not mapped'}\n"
                f"- approval_policy: {self.config.approval_policy}\n"
                f"- approval_mode: {self.config.approval_mode}\n"
                f"- sandbox: {self._effective_sandbox(room_id) or 'default'}\n"
                f"- reasoning_effort: {self.config.reasoning_effort or 'default'}\n"
                f"- reasoning_summary: {self.config.reasoning_summary or 'default'}\n"
                f"- pending_approvals: {self._open_approval_count(room_id)}\n"
                f"- session_approvals: {session_approvals}\n"
                f"- token_usage: {usage_line}\n"
                f"- turn_task_markers: {self.config.emit_turn_task_markers}"
            )
            await deliver_notice(tools, status_text, mentions=mention)
            return True

        if command in {CodexCommand.MODEL, CodexCommand.MODELS}:
            model_arg = args.strip()
            if not model_arg:
                await deliver_notice(
                    tools,
                    "Current model: "
                    f"`{self._selected_model or 'unknown'}` "
                    f"(configured: `{self.config.model or 'auto'}`). "
                    f"Use `/{CodexCommand.MODEL} {CodexSubcommand.LIST}` to view available "
                    f"models or `/{CodexCommand.MODEL} <id>` to override.",
                    mentions=mention,
                )
                return True

            if model_arg.lower() in _MODEL_LIST_WORDS:
                client = self._require_active_client_state().require_client()
                result = await client.request(CodexRequestMethod.MODEL_LIST, {})
                models = self._visible_model_ids(result)
                if models:
                    preview = ", ".join(models[:10])
                    if len(models) > 10:
                        preview += ", ..."
                    await deliver_notice(
                        tools,
                        f"Available models ({len(models)}): {preview}",
                        mentions=mention,
                    )
                else:
                    await deliver_notice(
                        tools,
                        "No visible models returned by Codex app-server.",
                        mentions=mention,
                    )
                return True

            # Model selection belongs to the room's process, never adapter-wide config.
            state = self._require_active_client_state()
            state.model_override = model_arg
            self._selected_model = model_arg
            await deliver_notice(
                tools,
                f"Model override set to `{model_arg}` for subsequent turns.",
                mentions=mention,
            )
            return True

        if command == CodexCommand.REASONING:
            effort_arg = args.strip().lower()
            model_id, efforts = await self._current_model_efforts()
            supported = ", ".join(efforts)
            if not effort_arg:
                hint = f"`{model_id}` supports: {supported}. " if efforts else ""
                await deliver_notice(
                    tools,
                    f"Current reasoning effort: `{self.config.reasoning_effort or 'default'}`. "
                    f"Summary: `{self.config.reasoning_summary or 'default'}`. "
                    f"{hint}Use `/reasoning <effort>` to override.",
                    mentions=mention,
                )
                return True
            # A person's typo would otherwise fail every later turn; the
            # setreasoning tool can't check this because it runs mid-turn.
            if efforts and effort_arg not in efforts:
                await deliver_notice(
                    tools,
                    f"`{model_id}` doesn't support reasoning effort `{effort_arg}`. "
                    f"Supported: {supported}.",
                    mentions=mention,
                )
                return True
            self._require_active_client_state().reasoning_effort = effort_arg
            await deliver_notice(
                tools,
                f"Reasoning effort set to `{effort_arg}` for subsequent turns.",
                mentions=mention,
            )
            return True

        # --- Phase 1: /sandbox and /permissions commands ---
        if command == CodexCommand.SANDBOX:
            if self.config.sandbox_policy is not None:
                await deliver_notice(
                    tools,
                    "Cannot override sandbox: a `sandbox_policy` is configured. "
                    "Remove `sandbox_policy` from config to use per-room "
                    f"`/{CodexCommand.SANDBOX}` overrides.",
                    mentions=mention,
                )
                return True
            mode_arg = args.strip()
            if not mode_arg:
                effective = self._effective_sandbox(room_id) or "default"
                await deliver_notice(
                    tools,
                    f"Current sandbox: `{effective}`. "
                    f"Use `/{CodexCommand.SANDBOX} <{_SANDBOX_MODE_CHOICES}>` to change.",
                    mentions=mention,
                )
                return True
            # Parse tokens explicitly so "--confirm-anything-else" is surfaced
            # as an unknown mode instead of silently stripped away.
            tokens = mode_arg.split()
            confirm_flag = _SANDBOX_CONFIRM_FLAG in tokens
            mode_tokens = [tok for tok in tokens if tok != _SANDBOX_CONFIRM_FLAG]
            if len(mode_tokens) != 1:
                await deliver_notice(
                    tools,
                    f"Usage: `/{CodexCommand.SANDBOX} <{_SANDBOX_MODE_CHOICES}> "
                    f"[{_SANDBOX_CONFIRM_FLAG}]`.",
                    mentions=mention,
                )
                return True
            mode_token = mode_tokens[0]
            normalized = self._normalize_sandbox_mode(mode_token)
            if normalized is None:
                await deliver_notice(
                    tools,
                    f"Invalid sandbox mode `{mode_token}`. "
                    f"Valid: {', '.join(CodexSandboxMode)}.",
                    mentions=mention,
                )
                return True
            escalating = normalized == CodexSandboxMode.DANGER_FULL_ACCESS
            if escalating and not confirm_flag:
                await deliver_notice(
                    tools,
                    f"Escalating to `{normalized}` removes all sandbox "
                    f"restrictions. Re-run with `{_SANDBOX_CONFIRM_FLAG}` to proceed:\n"
                    f"`/{CodexCommand.SANDBOX} {normalized} {_SANDBOX_CONFIRM_FLAG}`",
                    mentions=mention,
                )
                return True
            if escalating:
                logger.warning(
                    "Sandbox escalated to %s via /%s command in room %s by %s",
                    normalized,
                    CodexCommand.SANDBOX,
                    room_id,
                    msg.sender_name or msg.sender_type or "unknown",
                )
            self._sandbox_overrides[room_id] = normalized
            await deliver_notice(
                tools,
                f"Sandbox mode set to `{normalized}` for subsequent turns in this room.",
                mentions=mention,
            )
            return True

        if command == CodexCommand.PERMISSIONS:
            session_approved = self._session_approved.get(room_id) or ()
            audit = self._approval_audit.get(room_id, [])
            lines = ["Effective permissions:"]
            lines.append(f"- approval_mode: {self.config.approval_mode}")
            lines.append(f"- sandbox: {self._effective_sandbox(room_id) or 'default'}")
            lines.append(f"- approval_policy: {self.config.approval_policy}")
            if session_approved:
                lines.append(f"- session_approved patterns: {len(session_approved)}")
                for pattern in sorted(session_approved):
                    lines.append(f"  - {pattern}")
            if audit:
                lines.append(f"- approval_history: {len(audit)} decisions")
                for entry in audit[-5:]:
                    lines.append(
                        f"  - [{entry.timestamp}] {entry.method}: "
                        f"{entry.decision} by {entry.decided_by}"
                    )
            await deliver_notice(tools, "\n".join(lines), mentions=mention)
            return True

        # --- Phase 2: /threads, /thread info, /thread archive ---
        if command in {CodexCommand.THREADS, CodexCommand.THREAD}:
            subcommand = args.strip().lower()
            if command == CodexCommand.THREADS or not subcommand:
                # List all room->thread mappings
                if not self._room_threads:
                    await deliver_notice(
                        tools, "No active thread mappings.", mentions=mention
                    )
                    return True
                lines = ["Active thread mappings:"]
                for rid, tid in self._room_threads.items():
                    current = " (current)" if rid == room_id else ""
                    lines.append(f"- room `{rid}` → thread `{tid}`{current}")
                await deliver_notice(tools, "\n".join(lines), mentions=mention)
                return True

            if subcommand == CodexSubcommand.INFO:
                mapped_thread = self._room_threads.get(room_id)
                if not mapped_thread:
                    await deliver_notice(
                        tools, "No thread mapped for this room.", mentions=mention
                    )
                    return True
                usage = self._token_usage.get(mapped_thread)
                usage_line = (
                    usage.format_summary()
                    if usage and usage.total_tokens > 0
                    else "none"
                )
                info_text = (
                    f"Thread info:\n"
                    f"- thread_id: {mapped_thread}\n"
                    f"- room_id: {room_id}\n"
                    f"- token_usage: {usage_line}"
                )
                await deliver_notice(tools, info_text, mentions=mention)
                return True

            if subcommand == CodexSubcommand.ARCHIVE:
                mapped_thread = self._forget_room_thread(room_id)
                self._require_active_client_state().forget_thread()
                await deliver_notice(
                    tools,
                    f"Thread `{mapped_thread or 'none'}` archived. "
                    "A new thread will be created on next message.",
                    mentions=mention,
                )
                return True

            return False

        # --- Phase 4: /usage command ---
        if command == CodexCommand.USAGE:
            mapped_thread = self._room_threads.get(room_id)
            if not mapped_thread:
                await deliver_notice(
                    tools,
                    "No thread mapped for this room — no usage data.",
                    mentions=mention,
                )
                return True
            usage = self._token_usage.get(mapped_thread)
            if not usage or usage.total_tokens == 0:
                await deliver_notice(
                    tools,
                    "No token usage recorded for this thread.",
                    mentions=mention,
                )
                return True
            await deliver_notice(
                tools,
                f"Thread `{mapped_thread}` — {usage.format_summary()}",
                mentions=mention,
            )
            return True

        return False

    async def _handle_approval_command(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        command: CodexCommand,
        args: str,
    ) -> bool:
        mention = self._sender_mention(msg)
        pending = self._pending_approvals.get(room_id) or DecisionRegistry()

        if command == CodexCommand.APPROVALS:
            if not (open_entries := pending.unclaimed()):
                await deliver_notice(tools, "No pending approvals.", mentions=mention)
                return True
            lines = ["Pending approvals:"]
            now = datetime.now(UTC)
            for entry in open_entries:
                age_s = int((now - entry.payload.created_at).total_seconds())
                lines.append(f"- {entry.token}: {entry.payload.summary} ({age_s}s)")
            await deliver_notice(tools, "\n".join(lines), mentions=mention)
            return True

        if (decision_value := _APPROVAL_COMMAND_DECISIONS.get(command)) is None:
            return False

        if not pending:
            await deliver_notice(
                tools,
                NO_APPROVALS_TO_RESOLVE_MESSAGE,
                mentions=mention,
            )
            return True

        open_ids = sorted(entry.token for entry in pending.unclaimed())
        token = args.strip().split(" ", 1)[0] if args.strip() else ""
        if not token:
            match open_ids:
                case []:
                    await deliver_notice(
                        tools, NO_APPROVALS_TO_RESOLVE_MESSAGE, mentions=mention
                    )
                    return True
                case [only]:
                    token = only
                case _:
                    await deliver_notice(
                        tools,
                        "Multiple approvals pending. "
                        f"Use `/{command} <id>`. Pending: {', '.join(open_ids)}",
                        mentions=mention,
                    )
                    return True

        if (selected := pending.get(token)) is None:
            await deliver_notice(
                tools,
                f"Unknown approval id `{token}`. "
                f"Pending: {', '.join(open_ids) or 'none'}",
                mentions=mention,
            )
            return True

        is_session = command == CodexCommand.APPROVE_SESSION
        # Session-level approval needs a non-empty key (e.g. a concrete command
        # string) so future requests can match.  Reject early so we never store
        # an empty string in _session_approved or report a misleading
        # "Future `` requests will be auto-approved" message to the user.
        if is_session and not selected.session_key:
            await deliver_notice(
                tools,
                f"Approval `{token}` cannot be resolved as session-level: "
                "this request has no command signature to match against. "
                f"Use `/{CodexCommand.APPROVE} {token}` for a one-shot approval instead.",
                mentions=mention,
            )
            return True

        if pending.try_claim(token) is None:
            await deliver_notice(
                tools,
                f"Approval `{token}` is no longer pending.",
                mentions=mention,
            )
            return True

        selected.future.set_result(decision_value)

        # Session-level: register the session key for auto-approval
        if is_session:
            self._record_session_approval(room_id, selected.session_key)
            await deliver_notice(
                tools,
                APPROVAL_RESOLVED_TEMPLATE.format(token=token, decision=decision_value)
                + " This session-level approval auto-approves future "
                f"`{selected.session_key}` requests.",
                mentions=mention,
            )
        else:
            await deliver_notice(
                tools,
                APPROVAL_RESOLVED_TEMPLATE.format(token=token, decision=decision_value),
                mentions=mention,
            )
        return True

    def _build_system_prompt(self) -> None:
        if self.config.system_prompt:
            self._system_prompt = (
                f"{self.config.system_prompt}\n\n{COMMUNICATION_INSTRUCTIONS}"
            )
            return

        self._system_prompt = render_system_prompt(
            agent_name=self.agent_name or "Agent",
            agent_description=self.agent_description or "An AI assistant",
            custom_section=self.config.custom_section,
            include_base_instructions=self.config.include_base_instructions,
            features=self.features,
        )
        if not self.config.include_base_instructions:
            self._system_prompt += f"\n\n{COMMUNICATION_INSTRUCTIONS}"

    def _apply_turn_overrides(
        self, params: dict[str, Any], *, room_id: str | None = None
    ) -> None:
        state = self._require_active_client_state()
        params["model"] = self._selected_model
        params["cwd"] = state.workspace
        params["approvalPolicy"] = self.config.approval_policy
        params["personality"] = self.config.personality
        if state.reasoning_effort or self.config.reasoning_effort:
            params["effort"] = state.reasoning_effort or self.config.reasoning_effort
        if state.reasoning_summary or self.config.reasoning_summary:
            params["summary"] = state.reasoning_summary or self.config.reasoning_summary
        self._apply_turn_sandbox(params, room_id=room_id)

    def _apply_thread_sandbox(
        self, params: dict[str, Any], *, room_id: str | None = None
    ) -> None:
        """Apply sandbox to thread/start params (only SandboxMode is accepted)."""
        if self.config.sandbox_policy is not None:
            # thread/start only has a `sandbox` field (SandboxMode enum).
            # Extract mode from sandbox_policy when possible.
            policy_type = self.config.sandbox_policy.get("type")
            if isinstance(policy_type, str):
                mode = self._normalize_sandbox_mode(policy_type)
                if mode is not None:
                    params["sandbox"] = mode
                    return
            logger.warning(
                "sandbox_policy type %s is not representable on thread/start; "
                "it will be applied at turn level instead",
                policy_type,
            )
            return

        effective = self._effective_sandbox(room_id)
        if effective is None:
            return

        sandbox_mode = self._normalize_sandbox_mode(effective)
        if sandbox_mode is not None:
            params["sandbox"] = sandbox_mode
            return

        if self._canonical_sandbox_key(effective) == _EXTERNAL_SANDBOX_KEY:
            # externalSandbox is only representable via sandboxPolicy on
            # turn/start; thread/start does not accept it.
            logger.debug(
                "external-sandbox will be applied at turn level, not on thread/start"
            )
            return

        logger.warning("Ignoring unsupported Codex sandbox value: %s", effective)

    # Codex app-server has two sandbox fields with different wire formats:
    # thread/start.sandbox takes a kebab-case CodexSandboxMode, while
    # turn/start.sandboxPolicy takes a camelCase SandboxPolicy type tag.
    # This mapping bridges the two (see codex-app-server protocol types).
    _SANDBOX_MODE_TO_POLICY_TYPE: ClassVar[dict[str, str]] = {
        CodexSandboxMode.READ_ONLY: "readOnly",
        CodexSandboxMode.WORKSPACE_WRITE: "workspaceWrite",
        CodexSandboxMode.DANGER_FULL_ACCESS: "dangerFullAccess",
    }

    def _apply_turn_sandbox(
        self, params: dict[str, Any], *, room_id: str | None = None
    ) -> None:
        """Apply sandbox to turn/start params (full SandboxPolicy is accepted)."""
        if self.config.sandbox_policy is not None:
            params["sandboxPolicy"] = self._normalize_sandbox_policy(
                self.config.sandbox_policy
            )
            return

        effective = self._effective_sandbox(room_id)
        if effective is None:
            return

        sandbox_mode = self._normalize_sandbox_mode(effective)
        if sandbox_mode is not None:
            policy_type = self._SANDBOX_MODE_TO_POLICY_TYPE.get(sandbox_mode)
            if policy_type:
                params["sandboxPolicy"] = {"type": policy_type}
            return

        if self._canonical_sandbox_key(effective) == _EXTERNAL_SANDBOX_KEY:
            params["sandboxPolicy"] = {"type": _EXTERNAL_SANDBOX_POLICY_TYPE}
            return

        logger.warning("Ignoring unsupported Codex sandbox value: %s", effective)

    def _effective_sandbox(self, room_id: str | None = None) -> str | None:
        """Return the effective sandbox for *room_id*, checking per-room overrides first."""
        if room_id:
            override = self._sandbox_overrides.get(room_id)
            if override is not None:
                return override
        return self.config.sandbox

    @classmethod
    def _normalize_sandbox_mode(cls, sandbox: str) -> CodexSandboxMode | None:
        key = cls._canonical_sandbox_key(sandbox)
        return CodexSandboxMode(key) if key in _SANDBOX_MODES else None

    @classmethod
    def _normalize_sandbox_policy(
        cls, sandbox_policy: dict[str, Any]
    ) -> dict[str, Any]:
        normalized = dict(sandbox_policy)
        policy_type = normalized.get("type")
        if not isinstance(policy_type, str):
            return normalized

        key = cls._canonical_sandbox_key(policy_type)
        camel = cls._SANDBOX_MODE_TO_POLICY_TYPE.get(key)
        if camel:
            normalized["type"] = camel
        elif key == _EXTERNAL_SANDBOX_KEY:
            normalized["type"] = _EXTERNAL_SANDBOX_POLICY_TYPE
        return normalized

    @staticmethod
    def _canonical_sandbox_key(value: str) -> str:
        compact = value.strip().lower().replace("_", "-").replace(" ", "")
        return _SANDBOX_KEY_ALIASES.get(compact, compact)

    @staticmethod
    def _extract_turn_error(turn_payload: dict[str, Any]) -> str:
        error = turn_payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message
        return ""

    @staticmethod
    def _approval_summary(method: str, params: dict[str, Any]) -> str:
        match method:
            case CodexApprovalMethod.COMMAND_EXECUTION:
                command = params.get("command")
                if isinstance(command, str) and command:
                    return f"command: {command}"
                return "command execution"
            case CodexApprovalMethod.FILE_CHANGE:
                reason = params.get("reason")
                if isinstance(reason, str) and reason:
                    return f"file changes: {reason}"
                return "file changes"
            case _:
                return method

    @staticmethod
    def _approval_type(method: str) -> str:
        """Return a short label for the approval request type."""
        match method:
            case CodexApprovalMethod.COMMAND_EXECUTION:
                return CodexItemType.COMMAND_EXECUTION.value
            case CodexApprovalMethod.FILE_CHANGE:
                return CodexItemType.FILE_CHANGE.value
            case _:
                return method

    def _session_approval_key(self, method: str, params: dict[str, Any]) -> str:
        """Build a key for session-level auto-approval matching.

        When ``session_approval_granularity`` is ``"full_command"`` (default),
        the key includes the full command string so ``/approve-session`` on
        ``npm test`` only auto-approves future ``npm test`` requests.

        When set to ``"binary"``, keys on the command binary (first token) so
        ``/approve-session`` on ``npm test`` auto-approves all ``npm`` commands.

        For file changes, keys on the set of paths being modified so
        ``/approve-session`` on a request touching ``src/foo.py`` only
        auto-approves future requests touching the same path(s).  When no
        paths are present we return an empty string so session-level approval
        is refused — keying on the bare method string would turn
        ``/approve-session`` into a blanket "approve every future file change"
        switch, which is a security footgun.
        """
        match method:
            case CodexApprovalMethod.COMMAND_EXECUTION:
                command = params.get("command")
                if isinstance(command, str) and command.strip():
                    cmd = command.strip()
                    if self.config.session_approval_granularity == "binary":
                        return f"commandExecution:{cmd.split()[0]}"
                    return f"commandExecution:{cmd}"
                # No identifiable command — return empty so session-level
                # approval is not possible (avoids a blanket wildcard match).
                return ""

            case CodexApprovalMethod.FILE_CHANGE:
                paths = self._extract_file_change_paths(params)
                if not paths:
                    # No identifiable paths — refuse session-level approval so
                    # one /approve-session can't auto-approve every future
                    # file change in this room.
                    return ""
                # Sort for a stable key regardless of change order.
                return "fileChange:" + "|".join(sorted(paths))

        # Unknown method — refuse session-level approval rather than key on
        # the bare method string (which would collapse all future requests
        # of this method into one bucket).
        return ""

    @staticmethod
    def _extract_file_change_paths(params: dict[str, Any]) -> list[str]:
        """Extract file paths from an item/fileChange/requestApproval payload."""
        paths: list[str] = []
        changes = params.get("changes")
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict):
                    path = change.get("path")
                    if isinstance(path, str) and path:
                        paths.append(path)
        # Some payload variants carry paths at the top level.
        for key in ("path", "paths"):
            value = params.get(key)
            if isinstance(value, str) and value:
                paths.append(value)
            elif isinstance(value, list):
                for p in value:
                    if isinstance(p, str) and p:
                        paths.append(p)
        return paths

    @staticmethod
    def _task_event_id(params: dict[str, Any]) -> str | None:
        for key in ("taskId", "task_id"):
            value = params.get(key)
            if isinstance(value, str) and value:
                return value

        task_value = params.get("task")
        if isinstance(task_value, dict):
            for key in ("taskId", "task_id", "id"):
                nested = task_value.get(key)
                if isinstance(nested, str) and nested:
                    return nested
        return None

    @staticmethod
    def _task_event_title(params: dict[str, Any]) -> str | None:
        for key in ("title", "name"):
            value = params.get(key)
            if isinstance(value, str) and value:
                return value

        task_value = params.get("task")
        if isinstance(task_value, str) and task_value:
            return task_value
        if isinstance(task_value, dict):
            for key in ("title", "name", "description"):
                nested = task_value.get(key)
                if isinstance(nested, str) and nested:
                    return nested
        return None

    @staticmethod
    def _task_event_summary(params: dict[str, Any]) -> str | None:
        for key in ("summary", "result", "message", "description"):
            value = params.get(key)
            if isinstance(value, str) and value:
                return value

        task_value = params.get("task")
        if isinstance(task_value, dict):
            for key in ("summary", "result", "message", "description"):
                nested = task_value.get(key)
                if isinstance(nested, str) and nested:
                    return nested
        return None

    @staticmethod
    def _build_task_event_content(
        *,
        task_id: str | None,
        task: str,
        status: str,
        summary: str | None = None,
    ) -> str:
        lines: list[str] = []
        if task_id:
            lines.append(f"UUID: {task_id}")
        lines.append(f"Task: {task}")
        lines.append(f"Status: {status}")
        if summary and summary != task:
            lines.append(f"Summary: {summary}")
        return "\n".join(lines)

    @staticmethod
    def _sender_mention(msg: PlatformMessage) -> list[dict[str, str]]:
        return [{"id": msg.sender_id, "name": msg.sender_name or msg.sender_type}]

    @staticmethod
    def _extract_local_command(content: str) -> tuple[CodexCommand, str] | None:
        """Return ``(command, args)`` when ``content`` opens with a slash command.

        A delivered room message always leads with the platform's ``@handle``
        mention block (a message reaches an agent only by mentioning it), so
        that block is stripped first -- otherwise the command never leads the
        content and ``/approve``/``/decline`` silently miss. Only the token
        that remains in front is considered, so prose merely *containing* a
        slash word ("don't /approve it yet") stays prose instead of resolving
        to the command it argues against.
        """
        stripped = strip_leading_mentions(content).lstrip()
        if not stripped.startswith("/"):
            return None
        # Split on any whitespace, so a tab- or newline-separated argument still
        # reaches the command it belongs to.
        parts = stripped.removeprefix("/").split(maxsplit=1)
        word = parts[0].lower() if parts else ""
        if word not in _COMMAND_WORDS:
            return None
        return CodexCommand(word), parts[1].strip() if len(parts) > 1 else ""

    @staticmethod
    def _approval_token(request_id: int | str, params: dict[str, Any]) -> str:
        for key in ("approvalId", "approval_id", "itemId", "callId"):
            value = params.get(key)
            if isinstance(value, str) and value:
                return value
        return f"req-{request_id}"

    def _open_approval_count(self, room_id: str) -> int:
        registry = self._pending_approvals.get(room_id)
        return registry.unclaimed_count() if registry else 0

    def _clear_pending_approval(
        self, room_id: str, entry: DecisionEntry[PendingApproval]
    ) -> None:
        registry = self._pending_approvals.get(room_id)
        if registry is None:
            return
        registry.forget(entry)
        if len(registry) == 0:
            self._pending_approvals.pop(room_id, None)

    def _clear_pending_approvals_for_room(self, room_id: str) -> None:
        registry = self._pending_approvals.pop(room_id, None)
        if registry is None:
            return
        for entry in registry.cancel_all():
            entry.payload.future.set_result("decline")

    @staticmethod
    def _visible_model_ids(result: dict[str, Any]) -> list[str]:
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            return []
        models: list[str] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            model_id = entry.get("id")
            if not isinstance(model_id, str) or not model_id:
                continue
            if bool(entry.get("hidden", False)):
                continue
            models.append(model_id)
        return models

    @staticmethod
    def _supported_efforts(result: dict[str, Any], model_id: str) -> list[str]:
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            return []
        entry = next(
            (e for e in data if isinstance(e, dict) and e.get("id") == model_id),
            None,
        )
        efforts = entry.get("supportedReasoningEfforts") if entry else None
        if not isinstance(efforts, list):
            return []
        return [
            effort["reasoningEffort"]
            for effort in efforts
            if isinstance(effort, dict)
            and isinstance(effort.get("reasoningEffort"), str)
        ]

    async def _current_model_efforts(self) -> tuple[str, list[str]]:
        """The room's model and the efforts Codex reports for it (empty if unknown)."""
        room = self._require_active_client_state()
        client = room.require_client()
        model_id = room.selected_model or await self._select_model(room, client)
        try:
            result = await client.request(CodexRequestMethod.MODEL_LIST, {})
        except Exception:
            logger.warning(
                "model/list failed; supported reasoning efforts are unknown",
                exc_info=True,
            )
            return model_id, []
        return model_id, self._supported_efforts(result, model_id)
