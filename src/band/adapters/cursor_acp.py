"""Cursor CLI adapter over ACP."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

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
)
from band.integrations.acp.client_types import ACPClientSessionState
from band.integrations.acp.client_runtime import select_allow_option_id
from band.integrations.acp.session_config import SessionConfigResolver
from band.runtime.custom_tools import CustomToolDef
from band.runtime.formatters import strip_leading_mentions

logger = logging.getLogger(__name__)

DEFAULT_CURSOR_ACP_COMMAND: tuple[str, ...] = ("agent", "acp")
ApprovalMode = Literal["manual", "auto_accept", "auto_decline"]
QuestionMode = Literal["manual", "auto_first", "auto_cancel"]
PlanMode = Literal["manual", "auto_accept", "auto_decline"]
DecisionKind = Literal["permission", "question", "plan"]
_INVALID_DECISION = object()


@dataclass(frozen=True)
class CursorACPAdapterConfig:
    """Runtime configuration for Cursor's ``agent acp`` backend."""

    command: tuple[str, ...] = DEFAULT_CURSOR_ACP_COMMAND
    cwd: str | None = None
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
    max_pending_decisions: int = 10
    decision_authorized_senders: frozenset[str] | None = None


@dataclass
class CursorTurn:
    """The room context for the one Cursor extension-capable prompt."""

    room_id: str
    tools: AgentToolsProtocol
    requester_id: str | None
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
        self._pending_decisions: dict[str, PendingDecision] = {}
        env = self._cursor_env(config)
        super().__init__(
            command=list(config.command),
            cwd=config.cwd,
            env=env,
            auth_method="cursor_login",
            profile=self._cursor_profile,
            additional_tools=additional_tools,
            custom_section=config.custom_section,
            inject_band_tools=config.inject_band_tools,
            mcp_servers=config.mcp_servers,
            resolve_session_config=config.resolve_session_config,
            resolve_permission=self._resolve_cursor_permission,
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

        async with self._turn_lock:
            self._active_turn = CursorTurn(
                room_id=room_id,
                tools=tools,
                requester_id=msg.sender_id,
            )
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
                self._active_turn = None
                self._cancel_room_decisions(room_id)

    async def _get_or_create_session(
        self,
        room_id: str,
        history: ACPClientSessionState | None,
    ) -> tuple[str, bool]:
        session_id, created = await super()._get_or_create_session(room_id, history)
        turn = self._active_turn
        if turn is not None and turn.room_id == room_id:
            turn.session_id = session_id
            self._cursor_profile.bind_session(session_id)
        return session_id, created

    async def on_cleanup(self, room_id: str) -> None:
        session_id = self._room_to_session.get(room_id)
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
                choices = {"permission": self._option_ids(request.options)}
                result = await self._wait_for_decision(
                    kind="permission",
                    turn=turn,
                    choices=choices,
                    prompt=(
                        f"Cursor needs permission to run `{request.tool_call.name}`. "
                        "Reply `/cursor select {token} <option-id>` or "
                        "`/cursor deny {token}`. Available options: "
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
        choices = self._question_choices(params)
        if not choices:
            return {"outcome": {"outcome": "cancelled"}}
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
                result = await self._wait_for_decision(
                    kind="question",
                    turn=turn,
                    choices=choices,
                    multi_select=self._multiple_choice_questions(params),
                    prompt=(
                        "Cursor needs input. Reply `/cursor answer {token} "
                        "question-id=option-id[,option-id] ...`. Questions: "
                        + self._question_summary(params)
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
                        f"{description} needs approval. Reply `/cursor accept {{token}}` "
                        "or `/cursor reject {token}`."
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
        token = uuid4().hex[:8]
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._evict_oldest_decision()
        self._pending_decisions[token] = PendingDecision(
            kind=kind,
            room_id=turn.room_id,
            future=future,
            choices=choices or {},
            multi_select=multi_select,
        )
        try:
            try:
                await turn.tools.send_message(
                    prompt.replace("{token}", token),
                    mentions=[turn.requester_id] if turn.requester_id else None,
                )
            except Exception:
                logger.warning("Could not deliver Cursor %s decision prompt", kind)
                return None
            try:
                return await asyncio.wait_for(
                    future, timeout=self._config.decision_timeout_s
                )
            except TimeoutError:
                try:
                    await turn.tools.send_message(
                        f"Cursor {kind} decision `{token}` timed out and was cancelled.",
                        mentions=[turn.requester_id] if turn.requester_id else None,
                    )
                except Exception:
                    logger.warning("Could not deliver Cursor %s timeout notice", kind)
                return None
        finally:
            pending = self._pending_decisions.pop(token, None)
            if pending is not None and not pending.future.done():
                pending.future.set_result(None)

    async def _handle_control_message(
        self, msg: PlatformMessage, tools: AgentToolsProtocol, room_id: str
    ) -> bool:
        words = strip_leading_mentions(msg.content).strip().split()
        if not words or words[0].lower() != "/cursor":
            return False
        if len(words) == 1 or words[1].lower() == "decisions":
            await self._list_decisions(tools, room_id)
            return True
        if len(words) < 3:
            await tools.send_message(
                "Use `/cursor decisions` to list pending Cursor decisions."
            )
            return True
        action, token = words[1].lower(), words[2]
        pending = self._pending_decisions.get(token)
        if pending is None or pending.room_id != room_id:
            await tools.send_message(f"Cursor decision `{token}` is not pending.")
            return True
        if not self._is_authorized(msg.sender_id):
            await tools.send_message(
                "You are not authorized to resolve Cursor decisions."
            )
            return True
        result = self._command_result(action, words[3:], pending)
        if result is _INVALID_DECISION:
            await tools.send_message(
                f"That command is not valid for Cursor {pending.kind} decision `{token}`."
            )
            return True
        if not pending.future.done():
            pending.future.set_result(result)
        await tools.send_message(f"Cursor {pending.kind} decision `{token}` resolved.")
        return True

    def _command_result(
        self, action: str, args: list[str], pending: PendingDecision
    ) -> object:
        match pending.kind, action:
            case "permission", "deny":
                return None
            case "permission", "select" if len(args) == 1:
                return (
                    args[0]
                    if args[0] in pending.choices["permission"]
                    else _INVALID_DECISION
                )
            case "plan", "accept":
                return {"outcome": {"outcome": "accepted"}}
            case "plan", "reject":
                return {"outcome": {"outcome": "rejected"}}
            case "question", "answer":
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
    def _question_choices(params: dict[str, object]) -> dict[str, tuple[str, ...]]:
        return {
            question_id: tuple(option_id for option_id, _ in options)
            for question_id, (_, options) in CursorACPAdapter._question_details(
                params
            ).items()
        }

    @staticmethod
    def _question_details(
        params: dict[str, object],
    ) -> dict[str, tuple[str | None, tuple[tuple[str, str], ...]]]:
        """Project the valid question IDs, prompts, and labeled options once."""
        questions = params.get("questions")
        if not isinstance(questions, list):
            return {}
        details: dict[str, tuple[str | None, tuple[tuple[str, str], ...]]] = {}
        for question in questions:
            if not isinstance(question, Mapping):
                continue
            question_id, options = question.get("id"), question.get("options")
            if not isinstance(question_id, str) or not isinstance(options, list):
                continue
            option_details = tuple(
                (
                    option_id,
                    label
                    if isinstance((label := option.get("label")), str)
                    else option_id,
                )
                for option in options
                if isinstance(option, Mapping)
                and isinstance((option_id := option.get("id")), str)
            )
            if option_details:
                prompt = question.get("prompt")
                details[question_id] = (
                    prompt if isinstance(prompt, str) else None,
                    option_details,
                )
        return details

    @staticmethod
    def _multiple_choice_questions(params: dict[str, object]) -> frozenset[str]:
        """The question ids that permit more than one selected option."""
        questions = params.get("questions")
        if not isinstance(questions, list):
            return frozenset()
        return frozenset(
            question_id
            for question in questions
            if isinstance(question, dict)
            and question.get("allowMultiple") is True
            and isinstance((question_id := question.get("id")), str)
        )

    @staticmethod
    def _question_summary(params: dict[str, object]) -> str:
        return "; ".join(
            f"{question_id}: {prompt} ({', '.join(f'{option_id}={label}' for option_id, label in options)})"
            for question_id, (prompt, options) in CursorACPAdapter._question_details(
                params
            ).items()
            if prompt is not None
        )

    @staticmethod
    def _option_ids(options: tuple[object, ...]) -> tuple[str, ...]:
        """Return offered permission IDs in the runtime's advertised order."""
        option_ids: list[str] = []
        for option in options:
            option_id = (
                option.get("optionId", option.get("option_id"))
                if isinstance(option, Mapping)
                else getattr(option, "option_id", None)
            )
            if isinstance(option_id, str):
                option_ids.append(option_id)
        return tuple(option_ids)

    def _active_turn_for(self, room_id: str, session_id: str) -> CursorTurn | None:
        turn = self._active_turn
        return (
            turn
            if turn and (turn.room_id, turn.session_id) == (room_id, session_id)
            else None
        )

    def _is_authorized(self, sender_id: str | None) -> bool:
        allowed = self._config.decision_authorized_senders
        return allowed is None or sender_id in allowed

    def _evict_oldest_decision(self) -> None:
        if len(self._pending_decisions) < self._config.max_pending_decisions:
            return
        oldest_token = next(iter(self._pending_decisions))
        pending = self._pending_decisions.pop(oldest_token)
        if not pending.future.done():
            pending.future.set_result(None)

    def _cancel_room_decisions(self, room_id: str) -> None:
        for token, pending in tuple(self._pending_decisions.items()):
            if pending.room_id == room_id:
                self._pending_decisions.pop(token)
                if not pending.future.done():
                    pending.future.set_result(None)

    def _cancel_all_decisions(self) -> None:
        for pending in self._pending_decisions.values():
            if not pending.future.done():
                pending.future.set_result(None)
        self._pending_decisions.clear()

    async def _list_decisions(self, tools: AgentToolsProtocol, room_id: str) -> None:
        pending = [
            f"`{token}` ({decision.kind})"
            for token, decision in self._pending_decisions.items()
            if decision.room_id == room_id
        ]
        content = "Pending Cursor decisions: " + (", ".join(pending) or "none")
        await tools.send_message(content)


__all__ = [
    "ApprovalMode",
    "CursorACPAdapter",
    "CursorACPAdapterConfig",
    "DEFAULT_CURSOR_ACP_COMMAND",
    "PlanMode",
    "QuestionMode",
]
