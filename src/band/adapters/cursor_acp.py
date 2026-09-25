"""Cursor CLI adapter over ACP."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from typing_extensions import Unpack

from band.core.protocols import AgentToolsProtocol
from band.core.types import FeatureKwargs, PlatformMessage
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPPermissionRequest,
)
from band.integrations.acp.client_profiles import (
    CURSOR_ASK_QUESTION_METHOD,
    CURSOR_CREATE_PLAN_METHOD,
    CursorACPClientProfile,
    CursorQuestion,
    parse_cursor_questions,
)
from band.integrations.acp.client_runtime import (
    ACPRuntime,
    permission_option_ids,
    select_allow_option_id,
)
from band.integrations.acp.client_types import ACPClientSessionState
from band.integrations.acp.session_config import SessionConfigResolver
from band.runtime.custom_tools import CustomToolDef
from band.runtime.decisions import ClaimOutcome, DecisionRegistry, Timeout
from band.runtime.formatters import strip_leading_mentions
from band.workspaces import WorkspaceResolver, workspace_resolver_for

logger = logging.getLogger(__name__)

DEFAULT_CURSOR_ACP_COMMAND: tuple[str, ...] = ("agent", "acp")
ApprovalMode = Literal["manual", "auto_accept", "auto_decline"]
QuestionMode = Literal["manual", "auto_first", "auto_cancel"]
PlanMode = Literal["manual", "auto_accept", "auto_decline"]
DecisionKind = Literal["permission", "question", "plan"]
_INVALID_DECISION = object()
DECISION_NOT_PENDING_TEMPLATE = "Cursor decision `{token}` is not pending."


class CursorCommandWord(StrEnum):
    """The `/cursor <word> ...` vocabulary this adapter's room commands accept.

    Single source for both the room-facing prompt text and the parser, so
    the two can't drift apart.
    """

    DECISIONS = "decisions"
    SELECT = "select"
    DENY = "deny"
    ACCEPT = "accept"
    REJECT = "reject"
    ANSWER = "answer"


@dataclass(frozen=True)
class CursorACPAdapterConfig:
    """Runtime configuration for Cursor's ``agent acp`` backend.

    ``cwd`` is a compatibility alias for a workspace root; prefer
    ``workspace_for_room`` for new code.
    """

    command: tuple[str, ...] = DEFAULT_CURSOR_ACP_COMMAND
    cwd: str | None = None
    workspace_for_room: WorkspaceResolver | None = None
    env: dict[str, str] | None = None
    api_key: str | None = None
    auth_token: str | None = None
    custom_section: str = ""
    inject_band_tools: bool = True
    mcp_servers: list[dict[str, object]] | None = None
    resolve_session_config: SessionConfigResolver | None = None
    approval_mode: ApprovalMode = "manual"
    question_mode: QuestionMode = "manual"
    plan_mode: PlanMode = "manual"
    decision_timeout_s: float = 300.0
    # A manual decision wait runs inside the turn, bounded by turn_timeout_s
    # (see ACPClientAdapter.on_message) -- default headroom above
    # decision_timeout_s so a decision can't be silently truncated by the
    # turn deadline before its own timeout fires.
    turn_timeout_s: float = 900.0
    max_pending_decisions: int = 10
    decision_authorized_senders: frozenset[str] | None = None


@dataclass
class CursorTurn:
    """The room context for the one Cursor extension-capable prompt."""

    room_id: str
    tools: AgentToolsProtocol
    requester_id: str | None
    release: asyncio.Future[None]
    session_id: str | None = None


@dataclass
class PendingDecision:
    """A room command waiting to settle one Cursor extension request."""

    kind: DecisionKind
    room_id: str
    future: asyncio.Future[object]
    choices: dict[str, tuple[str, ...]] = field(default_factory=dict)
    multi_select: frozenset[str] = frozenset()


class CursorACPAdapter(ACPClientAdapter):
    """Band adapter for Cursor's native ACP stdio server."""

    def __init__(
        self,
        config: CursorACPAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        config = config or CursorACPAdapterConfig()
        self._validate_config(config)
        self._config = config
        self._cursor_profile = CursorACPClientProfile(self._resolve_extension_method)
        self._turn_lock = asyncio.Lock()
        self._active_turn: CursorTurn | None = None
        self._pending_decisions: DecisionRegistry[PendingDecision] = DecisionRegistry(
            max_pending=config.max_pending_decisions,
            authorized_senders=config.decision_authorized_senders,
        )
        env = self._cursor_env(config)
        workspace_for_room = workspace_resolver_for(
            config.cwd, config.workspace_for_room
        )
        super().__init__(
            command=list(config.command),
            workspace_for_room=workspace_for_room,
            env=env,
            auth_method="cursor_login",
            profile=self._cursor_profile,
            additional_tools=additional_tools,
            custom_section=config.custom_section,
            inject_band_tools=config.inject_band_tools,
            mcp_servers=config.mcp_servers,
            resolve_session_config=config.resolve_session_config,
            resolve_permission=self._resolve_cursor_permission,
            turn_timeout_s=config.turn_timeout_s,
            **features,
        )

    @staticmethod
    def _validate_config(config: CursorACPAdapterConfig) -> None:
        if not config.command:
            raise ValueError("Cursor ACP command must not be empty")
        if config.api_key and config.auth_token:
            raise ValueError("set either api_key or auth_token, not both")
        if config.decision_timeout_s <= 0:
            raise ValueError("decision_timeout_s must be greater than zero")
        if config.decision_timeout_s >= config.turn_timeout_s:
            raise ValueError("decision_timeout_s must be less than turn_timeout_s")
        if config.max_pending_decisions <= 0:
            raise ValueError("max_pending_decisions must be greater than zero")

    @staticmethod
    def _cursor_env(config: CursorACPAdapterConfig) -> dict[str, str] | None:
        """Build the subprocess environment without overriding explicit variables."""
        env = dict(config.env or {})
        if config.api_key:
            env.setdefault("CURSOR_API_KEY", config.api_key)
        if config.auth_token:
            env.setdefault("CURSOR_AUTH_TOKEN", config.auth_token)
        return env or None

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
        if await self._handle_control_message(msg, tools, room_id):
            return

        # ext_method routing needs _turn_lock held for the whole turn (only
        # one room's session_id is bound on the shared _cursor_profile at a
        # time), but a decision reply for THIS room must never queue behind
        # it -- _handle_control_message above already bypasses the lock
        # unconditionally. So the turn body runs detached: on_message
        # returns the moment a decision opens (or immediately, if none ever
        # does), releasing the room's message queue for that reply while the
        # lock, and the turn, keep going in _run_turn.
        await self._turn_lock.acquire()
        release: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        turn = CursorTurn(
            room_id=room_id,
            tools=tools,
            requester_id=msg.sender_id,
            release=release,
        )
        self._active_turn = turn
        turn_task = asyncio.create_task(
            self._run_turn(
                turn,
                msg,
                tools,
                history,
                participants_msg,
                contacts_msg,
                is_session_bootstrap=is_session_bootstrap,
                room_id=room_id,
            )
        )
        self._background_tasks.add(turn_task)
        turn_task.add_done_callback(lambda task: self._on_turn_task_done(task, room_id))
        done, _ = await asyncio.wait(
            {release, turn_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if turn_task in done:
            # No decision ever opened -- surface completion/failure exactly
            # as before this turn body ran detached.
            await turn_task

    def _on_turn_task_done(self, task: asyncio.Task[None], room_id: str) -> None:
        """Like the inherited generic background-task sink, but room-tagged.

        A turn that's still running when its room cleans up (see
        ``on_cleanup``) keeps going detached; if it then fails, the generic
        sink's log line carries no room/session context to debug from.
        """
        self._background_tasks.discard(task)
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
        tools: AgentToolsProtocol,
        history: ACPClientSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        try:
            await super().on_message(
                msg,
                tools,
                history,
                participants_msg,
                contacts_msg,
                is_session_bootstrap=is_session_bootstrap,
                room_id=room_id,
            )
        finally:
            self._cursor_profile.bind_session(None)
            if self._active_turn is turn:
                self._active_turn = None
            self._cancel_room_decisions(room_id)
            if not turn.release.done():
                turn.release.set_result(None)
            self._turn_lock.release()

    async def _get_or_create_session(
        self,
        runtime: ACPRuntime,
        room_id: str,
        history: ACPClientSessionState | None,
    ) -> tuple[str, bool]:
        session_id, created = await super()._get_or_create_session(
            runtime, room_id, history
        )
        turn = self._active_turn
        if turn is not None and turn.room_id == room_id:
            turn.session_id = session_id
            self._cursor_profile.bind_session(session_id)
        return session_id, created

    async def on_cleanup(self, room_id: str) -> None:
        session_id = self._room_to_session.get(room_id)
        # Wakes any decision _run_turn is parked on; the task itself keeps
        # running detached and winds down on its own (via _on_background_task_done)
        # once the runtime this stops out from under it closes the connection.
        self._cancel_room_decisions(room_id)
        await super().on_cleanup(room_id)
        if session_id is not None:
            self._cursor_profile.forget_session(session_id)

    async def cleanup_all(self, *, final: bool = True) -> None:
        self._cancel_all_decisions()
        self._cursor_profile.clear_sessions()
        await super().cleanup_all(final=final)

    async def _resolve_cursor_permission(
        self, request: ACPPermissionRequest
    ) -> str | None:
        match self._config.approval_mode:
            case "auto_accept":
                return select_allow_option_id(request.options)
            case "auto_decline":
                return None
            case "manual":
                turn = self._active_turn_for(request.room_id, request.session_id)
                if turn is None:
                    return None
                choices = {"permission": permission_option_ids(request.options)}
                result = await self._wait_for_decision(
                    kind="permission",
                    turn=turn,
                    choices=choices,
                    prompt=(
                        f"Cursor needs permission to run `{request.tool_call.name}`. "
                        f"Reply `/cursor {CursorCommandWord.SELECT} {{token}} <option-id>` "
                        f"or `/cursor {CursorCommandWord.DENY} {{token}}`. "
                        f"Available options: "
                        f"{', '.join(sorted(choices['permission'])) or 'none'}"
                    ),
                )
                return result if isinstance(result, str) else None

    async def _resolve_extension_method(
        self, method: str, params: dict[str, object]
    ) -> dict[str, object]:
        turn = self._active_turn
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
        match self._config.question_mode:
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
                        f"Cursor needs input. Reply `/cursor {CursorCommandWord.ANSWER} "
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
        match self._config.plan_mode:
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
                    prompt=(
                        f"{description} needs approval. Reply "
                        f"`/cursor {CursorCommandWord.ACCEPT} {{token}}` or "
                        f"`/cursor {CursorCommandWord.REJECT} {{token}}`."
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
            room_id=turn.room_id,
            future=future,
            choices=choices or {},
            multi_select=multi_select,
        )
        self._evict_oldest_decision()
        token = self._pending_decisions.register(pending)
        try:
            await turn.tools.send_message(
                prompt.replace("{token}", token), mentions=_requester_mentions(turn)
            )
        except Exception:  # noqa: BLE001 -- best-effort room notify; any failure (network, REST, unresolved mention) should not block the decision wait below
            logger.warning("Could not deliver Cursor %s decision prompt", kind)
            self._pending_decisions.forget(token)
            return None
        finally:
            # Release on_message here, not only in _run_turn's finally --
            # the room needs its queue back the instant a decision is
            # outstanding, whether or not the prompt itself landed.
            if not turn.release.done():
                turn.release.set_result(None)

        result = await self._pending_decisions.wait(
            token, future, timeout_s=self._config.decision_timeout_s
        )
        if result is Timeout.TIMED_OUT:
            await self._notify_decision_timeout(turn, kind, token)
            return None
        return result

    @staticmethod
    async def _notify_decision_timeout(
        turn: CursorTurn, kind: DecisionKind, token: str
    ) -> None:
        try:
            await turn.tools.send_message(
                f"Cursor {kind} decision `{token}` timed out and was cancelled.",
                mentions=_requester_mentions(turn),
            )
        except Exception:  # noqa: BLE001 -- best-effort room notify; the decision has already timed out, so a delivery failure here changes nothing
            logger.warning("Could not deliver Cursor %s timeout notice", kind)

    async def _handle_control_message(
        self, msg: PlatformMessage, tools: AgentToolsProtocol, room_id: str
    ) -> bool:
        words = strip_leading_mentions(msg.content).strip().split()
        if not words or words[0].lower() != "/cursor":
            return False
        mentions = [msg.sender_id]
        if len(words) == 1 or words[1].lower() == CursorCommandWord.DECISIONS:
            await self._list_decisions(tools, room_id, mentions=mentions)
            return True
        if len(words) < 3:
            await tools.send_message(
                f"Use `/cursor {CursorCommandWord.DECISIONS}` to list pending "
                "Cursor decisions.",
                mentions=mentions,
            )
            return True
        action, token = words[1].lower(), words[2]
        pending = self._pending_decisions.get(token)
        if pending is None or pending.room_id != room_id:
            await tools.send_message(
                DECISION_NOT_PENDING_TEMPLATE.format(token=token), mentions=mentions
            )
            return True
        result = self._command_result(action, words[3:], pending)
        if result is _INVALID_DECISION:
            await tools.send_message(
                f"That command is not valid for Cursor {pending.kind} decision `{token}`.",
                mentions=mentions,
            )
            return True
        outcome = self._pending_decisions.claim_reply(token, msg.sender_id)
        match outcome:
            case ClaimOutcome.UNAUTHORIZED:
                reply = "You are not authorized to resolve Cursor decisions."
            case ClaimOutcome.NOT_PENDING:
                reply = DECISION_NOT_PENDING_TEMPLATE.format(token=token)
            case ClaimOutcome.CLAIMED:
                pending.future.set_result(result)
                reply = f"Cursor {pending.kind} decision `{token}` resolved."
        await tools.send_message(reply, mentions=mentions)
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
        turn = self._active_turn
        return (
            turn
            if turn and (turn.room_id, turn.session_id) == (room_id, session_id)
            else None
        )

    def _evict_oldest_decision(self) -> None:
        if (evicted := self._pending_decisions.evict_oldest()) is None:
            return
        logger.info(
            "Evicting oldest pending Cursor %s decision `%s` in room %s "
            "(max_pending_decisions reached)",
            evicted.payload.kind,
            evicted.token,
            evicted.payload.room_id,
        )
        evicted.payload.future.set_result(None)

    def _cancel_room_decisions(self, room_id: str) -> None:
        for entry in self._pending_decisions.cancel_all(
            lambda pending: pending.room_id == room_id
        ):
            logger.info(
                "Cancelling pending Cursor %s decision `%s` in room %s (room cleanup)",
                entry.payload.kind,
                entry.token,
                room_id,
            )
            entry.payload.future.set_result(None)

    def _cancel_all_decisions(self) -> None:
        for entry in self._pending_decisions.cancel_all():
            logger.info(
                "Cancelling pending Cursor %s decision `%s` in room %s "
                "(adapter shutdown)",
                entry.payload.kind,
                entry.token,
                entry.payload.room_id,
            )
            entry.payload.future.set_result(None)

    async def _list_decisions(
        self, tools: AgentToolsProtocol, room_id: str, *, mentions: list[str]
    ) -> None:
        pending = [
            f"`{entry.token}` ({entry.payload.kind})"
            for entry in self._pending_decisions.unclaimed()
            if entry.payload.room_id == room_id
        ]
        content = "Pending Cursor decisions: " + (", ".join(pending) or "none")
        await tools.send_message(content, mentions=mentions)


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
