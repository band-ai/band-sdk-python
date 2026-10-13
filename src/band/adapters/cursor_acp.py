"""Cursor CLI adapter over ACP."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import ClassVar, Literal, Self

from band_sdk_core import is_authorized_sender
from pydantic import Field, PositiveFloat, PositiveInt, model_validator
from typing_extensions import Unpack

from band.client.streaming import ControlMode
from band.core.exceptions import InputNotAccepted
from band.core.protocols import (
    TURN_FAILURE_PROVIDER,
    AgentToolsProtocol,
    generic_provider_failure,
)
from band.core.turn import judge_detached_turn
from band.core.types import ApprovalMode, FeatureKwargs, PlatformMessage
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
    ACPPermissionRequest,
)
from band.integrations.acp.client_profiles import (
    CURSOR_ASK_QUESTION_METHOD,
    CURSOR_CREATE_PLAN_METHOD,
    CursorACPClientProfile,
    CursorQuestion,
    parse_cursor_questions,
)
from band.integrations.acp.client_runtime import ACPRuntime
from band.integrations.acp.client_types import ACPClientSessionState, ACPRoom
from band.integrations.acp.cursor import (
    CURSOR_CLI_BINARY,
    DECISION_NOT_PENDING_TEMPLATE,
    DECISION_RESOLVED_TEMPLATE,
    DECISION_TIMED_OUT_TEMPLATE,
    DECISION_UNAUTHORIZED_MESSAGE,
    PERMISSION_REQUESTED_TEMPLATE,
    PLAN_REQUESTED_TEMPLATE,
    ROOM_COMMAND,
    CursorCommandWord,
    canonicalize_cursor_tool_name,
    is_cursor_band_tool,
)
from band.integrations.acp.permissions import (
    ALLOW_ALWAYS_KIND,
    ALLOW_ONCE_KIND,
    option_id_of_kind,
    permission_option_ids,
    select_allow_option_id,
)
from band.integrations.acp.session_config import SessionConfigResolver
from band.runtime.custom_tools import CustomToolDef
from band.runtime.decisions import (
    DecisionEntry,
    DecisionRegistry,
    Timeout,
)
from band.runtime.formatters import strip_leading_mentions
from band.runtime.tools.registry import BAND_MCP_SERVER_NAME
from band.workspaces import WorkspaceResolver

logger = logging.getLogger(__name__)

DEFAULT_CURSOR_ACP_COMMAND: tuple[str, ...] = (CURSOR_CLI_BINARY, "acp")
CursorAuthMethod = Literal["cursor_login"]
QuestionMode = Literal["manual", "auto_first", "auto_cancel"]
PlanMode = Literal["manual", "auto_accept", "auto_decline"]
DecisionKind = Literal["permission", "question", "plan"]
_INVALID_DECISION = object()


class CursorACPAdapterConfig(ACPClientAdapterConfig):
    """Settings for Cursor's ``agent acp`` backend.

    Inherits every :class:`ACPClientAdapterConfig` setting.

    Attributes:
        command: The ``agent acp`` launch command.
        auth_method: ACP ``authenticate`` method; off by default. Cursor reads
            ``api_key``, ``auth_token`` or a stored ``agent login`` without
            it, and never answers ``authenticate`` when no login is stored.
        api_key: Sets ``CURSOR_API_KEY`` unless ``env`` already does;
            exclusive with ``auth_token``.
        auth_token: Sets ``CURSOR_AUTH_TOKEN`` unless ``env`` already does.
        approval_mode: How Cursor's own tools are decided; ``"manual"`` asks
            the room. Band tools, including additional_tools, are approved
            once per call in every mode while the turn is active. If Cursor
            offers no allow-once option, the configured policy applies.
        question_mode: How Cursor's questions are answered; ``"manual"`` asks
            the room.
        plan_mode: How Cursor's plans are settled; ``"manual"`` asks the room.
        decision_timeout_s: Seconds a manual decision waits before it is
            cancelled. It runs inside the turn, so it must be shorter than
            ``turn_timeout_s``.
        turn_timeout_s: Turn deadline, with headroom above
            ``decision_timeout_s``.
        max_pending_decisions: Open manual decisions kept per room; the
            room's oldest is dropped when a new one would exceed it.
        decision_authorized_senders: Sender ids allowed to resolve decisions.
            ``None`` admits any room participant.
    """

    retention_unavailable_reason: ClassVar[str] = (
        "Cursor ACP subagent drainage does not establish whole-session inactivity "
        "or safe shutdown for other background work"
    )

    command: tuple[str, ...] = DEFAULT_CURSOR_ACP_COMMAND
    auth_method: CursorAuthMethod | None = None
    api_key: str | None = Field(default=None, repr=False)
    auth_token: str | None = Field(default=None, repr=False)
    approval_mode: ApprovalMode = "manual"
    question_mode: QuestionMode = "manual"
    plan_mode: PlanMode = "manual"
    decision_timeout_s: PositiveFloat = 300.0
    turn_timeout_s: PositiveFloat = 900.0
    max_pending_decisions: PositiveInt = 10
    decision_authorized_senders: frozenset[str] | None = None

    @model_validator(mode="after")
    def _check_auth_and_decision_deadline(self) -> Self:
        if self.api_key and self.auth_token:
            raise ValueError("set either api_key or auth_token, not both")
        if self.decision_timeout_s >= self.turn_timeout_s:
            raise ValueError("decision_timeout_s must be less than turn_timeout_s")
        return self


@dataclass
class CursorTurn:
    """One room message's prompt, from acceptance until its actual end.

    ``generation`` is the residency it prompts on, set when it takes the
    room; only the room's holder is extension-capable.
    """

    room: ACPRoom
    tools: AgentToolsProtocol
    requester_id: str | None
    release: asyncio.Future[None]
    task: asyncio.Task[None] | None = None
    generation: int | None = None
    session_id: str | None = None
    closed: bool = False
    # Cursor sends parallel tool calls' permission requests together and does
    # not apply an "allow always" to one already outstanding, so the room is
    # asked one at a time and a granted tool settles its queued repeats.
    permission_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    always_allowed: set[str] = field(default_factory=set)


@dataclass
class PendingDecision:
    """A room command waiting to settle one Cursor extension request."""

    kind: DecisionKind
    room_id: str
    future: asyncio.Future[object]
    choices: dict[str, tuple[str, ...]] = field(default_factory=dict)
    multi_select: frozenset[str] = frozenset()


def _close_turn(turn: CursorTurn) -> None:
    turn.closed = True
    turn.session_id = None


def _cancel(turn: CursorTurn) -> None:
    _close_turn(turn)
    if turn.task is not None:
        turn.task.cancel()


async def _report_unreported_failure(
    tools: AgentToolsProtocol, error: Exception
) -> None:
    """Report a detached turn's failure that nothing reported yet, as the
    runtime reports a failed attached turn."""
    if tools.turn.detached and not tools.turn.complete:
        await tools.send_failure(generic_provider_failure(TURN_FAILURE_PROVIDER, error))


def _detach(turn: CursorTurn) -> None:
    """Hand the room's message queue back while the turn keeps running."""
    if not turn.release.done():
        turn.tools.turn.detach()
        turn.release.set_result(None)


class CursorACPAdapter(ACPClientAdapter[CursorACPAdapterConfig]):
    """Band adapter for Cursor's native ACP stdio server."""

    def __init__(
        self,
        config: CursorACPAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        workspace_for_room: WorkspaceResolver | None = None,
        resolve_session_config: SessionConfigResolver | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        """Bridge Band rooms to Cursor's ``agent acp``.

        Args:
            config: The Cursor command, auth and decision settings.
            additional_tools: Custom tools served next to the Band tools.
            workspace_for_room: Maps a room id to its absolute workspace;
                exclusive with ``config.cwd``.
            resolve_session_config: Picks session config options.
        """
        config = config or CursorACPAdapterConfig()
        # Each room's one accepted turn, from acceptance until it actually
        # ends; independent rooms prompt concurrently.
        self._turns: dict[str, CursorTurn] = {}
        # The turn holding each room's lease, set and cleared inside that lease.
        self._holders: dict[str, CursorTurn] = {}
        # One capped registry per room, so no room evicts another's decision.
        self._pending_decisions: dict[str, DecisionRegistry[PendingDecision]] = {}
        super().__init__(
            config,
            additional_tools=additional_tools,
            workspace_for_room=workspace_for_room,
            resolve_session_config=resolve_session_config,
            resolve_permission=self._resolve_cursor_permission,
            **features,
        )

    def _credential_env(self) -> dict[str, str]:
        credentials = {
            "CURSOR_API_KEY": self.config.api_key,
            "CURSOR_AUTH_TOKEN": self.config.auth_token,
        }
        return {name: value for name, value in credentials.items() if value}

    def _canonical_tool_name(self, name: str) -> str:
        return canonicalize_cursor_tool_name(name, self._own_tool_names)

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: ACPClientSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        if await self.try_handle_control(msg, tools):
            return
        # A message never waits behind the room's turn: that turn may be parked
        # on a decision whose reply arrives after it, so the host keeps the
        # message and retries it once the room is free.
        if room_id in self._turns:
            raise InputNotAccepted("The room already has an active Cursor turn")

        # A decision reply for this room must never queue behind its parked
        # prompt, so the turn body runs detached: on_message returns the moment
        # a decision opens, while the task keeps the room's reservation and
        # lease through the turn's actual end.
        room = self._room(room_id)
        loop = asyncio.get_running_loop()
        release: asyncio.Future[None] = loop.create_future()
        turn = CursorTurn(
            room=room,
            tools=tools,
            requester_id=msg.sender_id,
            release=release,
        )
        turn_task = turn.task = room.retained.start_owned(
            partial(
                self._run_turn,
                turn,
                msg,
                history,
                participants_msg,
                contacts_msg,
                is_session_bootstrap=is_session_bootstrap,
            )
        )
        # Registered before it runs, so an interrupt during startup reaches it.
        self._accept(turn)
        turn_task.add_done_callback(partial(self._on_turn_task_done, turn=turn))
        try:
            done, _ = await asyncio.wait(
                {release, turn_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            # Until it detaches, the turn belongs to this delivery and ends
            # with it, as an inline OMP or Copilot turn does.
            _cancel(turn)
            await asyncio.wait({turn_task})
            raise
        if turn_task in done:
            # No decision ever opened: completion or failure is this turn's.
            await turn_task

    async def try_handle_control(
        self, msg: PlatformMessage, tools: AgentToolsProtocol
    ) -> bool:
        """Resolve or list the room's Cursor decisions without starting work."""
        if not await self._handle_control_message(msg, tools, msg.room_id):
            return False
        tools.turn.settle()
        return True

    def _accept(self, turn: CursorTurn) -> None:
        """Make ``turn`` its room's one accepted turn."""
        self._turns[turn.room.room_id] = turn

    def _forget_turn(self, turn: CursorTurn) -> None:
        if self._turns.get(turn.room.room_id) is turn:
            del self._turns[turn.room.room_id]

    def _on_turn_task_done(self, task: asyncio.Task[None], *, turn: CursorTurn) -> None:
        """Forget a turn that never ran and log a failure with its room."""
        self._forget_turn(turn)
        room_id = turn.room.room_id
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning(
                "Cursor turn for room %s failed in the background",
                room_id,
                exc_info=error,
            )

    async def _run_turn(
        self,
        turn: CursorTurn,
        msg: PlatformMessage,
        history: ACPClientSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
    ) -> None:
        room = turn.room
        try:
            async with (
                self._using_room(room, turn.tools) as runtime,
                self._holding(turn),
            ):
                await self._prompt_room(
                    room,
                    runtime,
                    msg,
                    turn.tools,
                    history,
                    participants_msg,
                    contacts_msg,
                    is_session_bootstrap=is_session_bootstrap,
                )
                # A failed or cancelled turn never gets here.
                await judge_detached_turn(turn.tools, room_id=room.room_id)
        finally:
            _close_turn(turn)
            self._forget_turn(turn)
            if not turn.release.done():
                turn.release.set_result(None)

    async def _get_or_create_session(
        self,
        room: ACPRoom,
        runtime: ACPRuntime,
        history: ACPClientSessionState | None,
    ) -> str:
        session_id = await super()._get_or_create_session(room, runtime, history)
        turn = self._holders.get(room.room_id)
        if turn is not None and turn.room is room:
            if turn.closed:
                raise asyncio.CancelledError
            turn.session_id = session_id
        return session_id

    def _client_profile(self, room: ACPRoom, generation: int) -> CursorACPClientProfile:
        """A profile bound to one residency: Cursor's extension requests carry
        no session id, so their origin is the process that sent them."""
        return CursorACPClientProfile(
            partial(self._resolve_extension_method, room, generation),
            active_session=partial(self._extension_session, room, generation),
        )

    @asynccontextmanager
    async def _holding(self, turn: CursorTurn) -> AsyncIterator[None]:
        """Make ``turn`` its room's holder for exactly its lease.

        Its failure is reported before the lease ends, so the next turn
        never takes the room while this one still answers for it.
        """
        room_id = turn.room.room_id
        self._take_room(turn)
        try:
            yield
        except Exception as error:
            await _report_unreported_failure(turn.tools, error)
            raise
        finally:
            self._cancel_room_decisions(room_id)
            del self._holders[room_id]

    def _take_room(self, turn: CursorTurn) -> None:
        """Make ``turn`` the holder of its room's current residency."""
        turn.generation = turn.room.generation
        self._holders[turn.room.room_id] = turn

    def _turn_of(self, room: ACPRoom, generation: int) -> CursorTurn | None:
        """The open prompt of exactly this room holder and of a residency
        recovery has not retired."""
        turn = self._holders.get(room.room_id)
        if (
            turn is None
            or turn.room is not room
            or turn.generation != generation
            or not room.retained.has_authority(generation)
            or turn.closed
        ):
            return None
        return turn

    def _extension_session(self, room: ACPRoom, generation: int) -> str | None:
        turn = self._turn_of(room, generation)
        return None if turn is None else turn.session_id

    async def on_cleanup(self, room_id: str) -> None:
        # Wakes any decision the turn is parked on; disposal then settles the
        # turn before the room's process closes.
        if (turn := self._turns.get(room_id)) is not None:
            _close_turn(turn)
        self._cancel_room_decisions(room_id)
        await super().on_cleanup(room_id)

    async def on_interrupt(self, room_id: str, mode: ControlMode) -> None:
        """End the room's accepted turn, starting, prompting or parked.

        The runtime's own interrupt cancels only the handler task, which a
        detached turn has already left. The turn is settled, so it is never
        reported as a missing reply, and cancelled; a cancelled prompt sends
        ``session/cancel``.
        """
        if (turn := self._turns.get(room_id)) is not None:
            turn.tools.turn.settle()
            _cancel(turn)
        self._cancel_room_decisions(room_id)

    async def cleanup_all(self, *, final: bool = True) -> None:
        self._cancel_all_decisions()
        await super().cleanup_all(final=final)

    async def _resolve_cursor_permission(
        self, request: ACPPermissionRequest
    ) -> str | None:
        call = request.tool_call
        is_band = (
            call.mcp_server == BAND_MCP_SERVER_NAME
            and call.name in self._own_tool_names
        ) or (
            call.mcp_server is None
            and is_cursor_band_tool(call.title or "", self._own_tool_names)
        )
        if is_band:
            if self._active_turn_for(request.room_id, request.session_id) is None:
                return None
            once = option_id_of_kind(request.options, ALLOW_ONCE_KIND)
            if once is not None:
                return once
        match self.config.approval_mode:
            case "auto_accept":
                return select_allow_option_id(request.options)
            case "auto_decline":
                return None
            case "manual":
                turn = self._active_turn_for(request.room_id, request.session_id)
                if turn is None:
                    return None
                async with turn.permission_lock:
                    return await self._ask_room_permission(turn, request)

    async def _ask_room_permission(
        self, turn: CursorTurn, request: ACPPermissionRequest
    ) -> str | None:
        tool = request.tool_call.name
        always = option_id_of_kind(request.options, ALLOW_ALWAYS_KIND)
        if tool in turn.always_allowed and always is not None:
            return always
        choices = {"permission": permission_option_ids(request.options)}
        result = await self._wait_for_decision(
            kind="permission",
            turn=turn,
            choices=choices,
            prompt=PERMISSION_REQUESTED_TEMPLATE.format(
                tool=tool,
                # _wait_for_decision fills the token in once it's minted.
                token="{token}",
                options=", ".join(sorted(choices["permission"])) or "none",
            ),
        )
        if not isinstance(result, str):
            return None
        if result == always:
            turn.always_allowed.add(tool)
        return result

    async def _resolve_extension_method(
        self,
        room: ACPRoom,
        generation: int,
        method: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        turn = self._turn_of(room, generation)
        if turn is None or turn.session_id is None:
            return {"outcome": {"outcome": "cancelled"}}
        match method:
            case value if value == CURSOR_ASK_QUESTION_METHOD:
                return await self._resolve_question(turn, params)
            case value if value == CURSOR_CREATE_PLAN_METHOD:
                return await self._resolve_plan(turn, params)
            case _:
                return {}

    async def _resolve_question(
        self, turn: CursorTurn, params: dict[str, object]
    ) -> dict[str, object]:
        questions = parse_cursor_questions(params)
        # A question with no valid option can never be answered, so a
        # complete reply is impossible to construct; cancel the whole
        # exchange rather than silently presenting the others as if it were
        # complete without it.
        if not questions or any(not question.options for question in questions):
            return {"outcome": {"outcome": "cancelled"}}
        choices = {
            question.id: tuple(option_id for option_id, _ in question.options)
            for question in questions
        }
        match self.config.question_mode:
            case "auto_first":
                return self._answered_questions(
                    {
                        question_id: [option_ids[0]]
                        for question_id, option_ids in choices.items()
                    }
                )
            case "auto_cancel":
                return {"outcome": {"outcome": "cancelled"}}
            case "manual":
                multi_select = frozenset(
                    question.id for question in questions if question.allow_multiple
                )
                result = await self._wait_for_decision(
                    kind="question",
                    turn=turn,
                    choices=choices,
                    multi_select=multi_select,
                    prompt=(
                        f"Cursor needs input. Reply `{ROOM_COMMAND} {CursorCommandWord.ANSWER} "
                        "{token} question-id=option-id[,option-id] ...`. Questions: "
                        + self._question_summary(questions)
                    ),
                )
                return (
                    result
                    if isinstance(result, dict)
                    else {"outcome": {"outcome": "cancelled"}}
                )

    async def _resolve_plan(
        self, turn: CursorTurn, params: dict[str, object]
    ) -> dict[str, object]:
        match self.config.plan_mode:
            case "auto_accept":
                return {"outcome": {"outcome": "accepted"}}
            case "auto_decline":
                return {"outcome": {"outcome": "rejected"}}
            case "manual":
                plan = params.get("plan", params.get("name"))
                description = plan if isinstance(plan, str) and plan else "Cursor plan"
                result = await self._wait_for_decision(
                    kind="plan",
                    turn=turn,
                    prompt=PLAN_REQUESTED_TEMPLATE.format(
                        plan=description,
                        # _wait_for_decision fills the token in once it's minted.
                        token="{token}",
                    ),
                )
                return (
                    result
                    if isinstance(result, dict)
                    else {"outcome": {"outcome": "cancelled"}}
                )

    async def _wait_for_decision(
        self,
        *,
        kind: DecisionKind,
        turn: CursorTurn,
        prompt: str,
        choices: dict[str, tuple[str, ...]] | None = None,
        multi_select: frozenset[str] = frozenset(),
    ) -> object | None:
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        pending = PendingDecision(
            kind=kind,
            room_id=turn.room.room_id,
            future=future,
            choices=choices or {},
            multi_select=multi_select,
        )
        decisions = self._room_decisions(turn.room.room_id)
        registration = decisions.register_minted(pending, room_id=turn.room.room_id)
        if registration.evicted is not None:
            self._resolve_evicted_decision(registration.evicted)
        entry = registration.entry
        try:
            await turn.tools.send_notice(
                prompt.replace("{token}", entry.token),
                mentions=_requester_mentions(turn),
            )
        except Exception:  # noqa: BLE001 -- best-effort room notify; any failure (network, REST, unresolved mention) should not block the decision wait below
            logger.warning("Could not deliver Cursor %s decision prompt", kind)
            # A reply that claimed it meanwhile owns the answer; wait for it.
            if decisions.withdraw(entry):
                return None
        finally:
            # Release on_message here, not only in _run_turn's finally --
            # the room needs its queue back the instant a decision is
            # outstanding, whether or not the prompt itself landed.
            _detach(turn)

        result = await decisions.wait(
            entry, future, timeout_s=self.config.decision_timeout_s
        )
        if result is Timeout.TIMED_OUT:
            await self._notify_decision_timeout(turn, kind, entry.token)
            return None
        return result

    @staticmethod
    async def _notify_decision_timeout(
        turn: CursorTurn, kind: DecisionKind, token: str
    ) -> None:
        try:
            await turn.tools.send_notice(
                DECISION_TIMED_OUT_TEMPLATE.format(kind=kind, token=token),
                mentions=_requester_mentions(turn),
            )
        except Exception:  # noqa: BLE001 -- best-effort room notify; the decision has already timed out, so a delivery failure here changes nothing
            logger.warning("Could not deliver Cursor %s timeout notice", kind)

    async def _handle_control_message(
        self, msg: PlatformMessage, tools: AgentToolsProtocol, room_id: str
    ) -> bool:
        words = strip_leading_mentions(msg.content).strip().split()
        if not words or words[0].lower() != ROOM_COMMAND:
            return False
        mentions = [msg.sender_id]
        if len(words) == 1 or words[1].lower() == CursorCommandWord.DECISIONS:
            await self._list_decisions(tools, room_id, mentions=mentions)
            return True
        if len(words) < 3:
            await tools.send_notice(
                f"Use `{ROOM_COMMAND} {CursorCommandWord.DECISIONS}` to list pending "
                "Cursor decisions.",
                mentions=mentions,
            )
            return True
        action, token = words[1].lower(), words[2]
        decisions = self._pending_decisions.get(room_id)
        pending = decisions.get(token) if decisions else None
        if decisions is None or pending is None:
            await tools.send_notice(
                DECISION_NOT_PENDING_TEMPLATE.format(token=token), mentions=mentions
            )
            return True
        result = self._command_result(action, words[3:], pending)
        if result is _INVALID_DECISION:
            await tools.send_notice(
                f"That command is not valid for Cursor {pending.kind} decision `{token}`.",
                mentions=mentions,
            )
            return True
        if not is_authorized_sender(
            self.config.decision_authorized_senders, msg.sender_id
        ):
            reply = DECISION_UNAUTHORIZED_MESSAGE
        elif decisions.try_claim(token) is None:
            reply = DECISION_NOT_PENDING_TEMPLATE.format(token=token)
        else:
            pending.future.set_result(result)
            reply = DECISION_RESOLVED_TEMPLATE.format(kind=pending.kind, token=token)
        await tools.send_notice(reply, mentions=mentions)
        return True

    def _command_result(
        self, action: str, args: list[str], pending: PendingDecision
    ) -> object:
        match pending.kind, action:
            case "permission", CursorCommandWord.DENY:
                return None
            case "permission", CursorCommandWord.SELECT if len(args) == 1:
                return (
                    args[0]
                    if args[0] in pending.choices["permission"]
                    else _INVALID_DECISION
                )
            case "plan", CursorCommandWord.ACCEPT:
                return {"outcome": {"outcome": "accepted"}}
            case "plan", CursorCommandWord.REJECT:
                return {"outcome": {"outcome": "rejected"}}
            case "question", CursorCommandWord.ANSWER:
                return self._answer_result(args, pending.choices, pending.multi_select)
            case _:
                return _INVALID_DECISION

    @staticmethod
    def _answer_result(
        args: list[str],
        choices: dict[str, tuple[str, ...]],
        multi_select: frozenset[str],
    ) -> dict[str, object] | object:
        selected: dict[str, list[str]] = {}
        for item in args:
            question_id, separator, raw_options = item.partition("=")
            option_ids = raw_options.split(",") if separator else []
            if (
                not question_id
                or question_id not in choices
                or question_id in selected
                or not option_ids
                or (len(option_ids) > 1 and question_id not in multi_select)
                or any(
                    option_id not in choices[question_id] for option_id in option_ids
                )
            ):
                return _INVALID_DECISION
            selected[question_id] = option_ids
        return (
            CursorACPAdapter._answered_questions(selected)
            if set(selected) == set(choices)
            else _INVALID_DECISION
        )

    @staticmethod
    def _answered_questions(selected: dict[str, list[str]]) -> dict[str, object]:
        return {
            "outcome": {
                "outcome": "answered",
                "answers": [
                    {"questionId": question_id, "selectedOptionIds": option_ids}
                    for question_id, option_ids in selected.items()
                ],
            }
        }

    @staticmethod
    def _question_summary(questions: tuple[CursorQuestion, ...]) -> str:
        return "; ".join(
            f"{question.id}: {question.prompt} ("
            f"{', '.join(f'{option_id}={label}' for option_id, label in question.options)})"
            for question in questions
        )

    def _active_turn_for(self, room_id: str, session_id: str) -> CursorTurn | None:
        turn = self._holders.get(room_id)
        return (
            turn if turn and not turn.closed and turn.session_id == session_id else None
        )

    def _room_decisions(self, room_id: str) -> DecisionRegistry[PendingDecision]:
        return self._pending_decisions.setdefault(
            room_id, DecisionRegistry(max_pending=self.config.max_pending_decisions)
        )

    @staticmethod
    def _resolve_evicted_decision(evicted: DecisionEntry[PendingDecision]) -> None:
        logger.info(
            "Evicting oldest pending Cursor %s decision `%s` in room %s "
            "(max_pending_decisions reached)",
            evicted.payload.kind,
            evicted.token,
            evicted.payload.room_id,
        )
        evicted.payload.future.set_result(None)

    def _cancel_room_decisions(self, room_id: str) -> None:
        decisions = self._pending_decisions.pop(room_id, None)
        if decisions is not None:
            _cancel_decisions(decisions, reason="room cleanup")

    def _cancel_all_decisions(self) -> None:
        rooms, self._pending_decisions = self._pending_decisions, {}
        for decisions in rooms.values():
            _cancel_decisions(decisions, reason="adapter shutdown")

    async def _list_decisions(
        self, tools: AgentToolsProtocol, room_id: str, *, mentions: list[str]
    ) -> None:
        decisions = self._pending_decisions.get(room_id)
        pending = [
            f"`{entry.token}` ({entry.payload.kind})"
            for entry in (decisions.unclaimed() if decisions else [])
        ]
        content = "Pending Cursor decisions: " + (", ".join(pending) or "none")
        await tools.send_notice(content, mentions=mentions)


def _cancel_decisions(
    decisions: DecisionRegistry[PendingDecision], *, reason: str
) -> None:
    for entry in decisions.cancel_all():
        logger.info(
            "Cancelling pending Cursor %s decision `%s` in room %s (%s)",
            entry.payload.kind,
            entry.token,
            entry.payload.room_id,
            reason,
        )
        entry.payload.future.set_result(None)


def _requester_mentions(turn: CursorTurn) -> list[str] | None:
    return [turn.requester_id] if turn.requester_id else None


__all__ = [
    "DEFAULT_CURSOR_ACP_COMMAND",
    "ApprovalMode",
    "CursorACPAdapter",
    "CursorACPAdapterConfig",
    "PlanMode",
    "QuestionMode",
]
