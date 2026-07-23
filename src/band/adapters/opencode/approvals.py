"""Per-room permission/question lifecycle for the OpenCode adapter.

OpenCode blocks a session mid-turn when its permission rules resolve to
``ask`` (``permission.asked``) or when the model uses the question tool
(``question.asked``); the session resumes only after a reply is POSTed.
``RoomApprovals`` owns that lifecycle for one room: the pending state, the
configured auto-reply modes, the manual relay to the room (and the parsing of
the user's ``approve``/``always``/``reject``/answer replies), and the expiry
timeouts. The adapter reaches it only through the narrow ``ApprovalPorts``
bundle, so the two mention sources — the open turn's sender for asks, the
control message's own sender for reply confirmations — are explicit.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from band.core.protocols import AgentToolsProtocol
from band.integrations.opencode import (
    OpencodeClientProtocol,
    OpencodePermissionRequest,
    OpencodeQuestion,
    OpencodeQuestionRequest,
)

from band.adapters.opencode.config import ApprovalReply, OpencodeAdapterConfig

logger = logging.getLogger(__name__)


@dataclass
class PendingPermission:
    request_id: str
    permission: str
    patterns: list[str]
    timeout_task: asyncio.Task[None] | None = None


@dataclass
class PendingQuestion:
    request_id: str
    questions: list[OpencodeQuestion]
    timeout_task: asyncio.Task[None] | None = None


@dataclass
class ApprovalPorts:
    """What the approval machinery needs from the adapter, per room."""

    room_id: str
    session_id: Callable[[], str | None]
    client: Callable[[], OpencodeClientProtocol | None]
    tools: Callable[[], AgentToolsProtocol | None]
    turn_mentions: Callable[[], list[dict[str, str]]]
    release_turn_wait: Callable[[], None]
    is_own_band_tool: Callable[[str], bool]


def parse_permission_reply(
    lowered_content: str, pending: PendingPermission
) -> ApprovalReply | None:
    """Map a room reply (``approve <id>`` / ``always <id>`` / ``reject <id>``)
    onto the OpenCode reply vocabulary; ``None`` when it is not a reply to
    this pending request."""
    tokens = lowered_content.split()
    if not tokens:
        return None

    command = tokens[0].lstrip("/")
    request_id = tokens[1] if len(tokens) > 1 else pending.request_id
    if request_id != pending.request_id:
        return None

    match command:
        case "approve":
            return "once"
        case "always":
            return "always"
        case "reject":
            return "reject"
    return None


def parse_question_answers(
    content: str, pending: PendingQuestion
) -> list[list[str]] | None:
    """One answer line per question; ``None`` when too few lines arrived."""
    if len(pending.questions) == 1:
        return [[content.strip()]]

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) < len(pending.questions):
        return None
    return [[line] for line in lines[: len(pending.questions)]]


def format_question_prompt(questions: list[OpencodeQuestion], request_id: str) -> str:
    prompt_lines = [f"OpenCode asked question `{request_id}`:"]
    for index, question in enumerate(questions, start=1):
        prompt_lines.append(f"{index}. {question.question}")
    prompt_lines.append("Reply with one line per question, or `reject`.")
    return "\n".join(prompt_lines)


class RoomApprovals:
    """Owns one room's pending permission/question state and its lifecycle."""

    def __init__(self, config: OpencodeAdapterConfig, ports: ApprovalPorts) -> None:
        self._config = config
        self._ports = ports
        self._pending_permission: PendingPermission | None = None
        self._pending_question: PendingQuestion | None = None

    async def on_permission_asked(self, request: OpencodePermissionRequest) -> None:
        request_id = request.id
        if not request_id:
            return

        # The adapter's own band tools are platform plumbing and must never
        # stall on an approval, in ANY mode (codex parity: it executes band
        # tools with no approval gate at all). Reply "always" so the server
        # installs an allow rule and stops asking; no pending state, no room
        # message -- the turn keeps running.
        if self._ports.is_own_band_tool(request.permission):
            await self._approve_own_band_tool(request_id)
            return

        pending = PendingPermission(
            request_id=request_id,
            permission=request.permission,
            patterns=request.patterns,
        )
        _cancel_timeout(self._pending_permission)
        self._pending_permission = pending

        if self._config.approval_mode == "auto_accept":
            await self._reply_permission("once")
            return

        if self._config.approval_mode == "auto_decline":
            await self._reply_permission("reject")
            return

        pending.timeout_task = asyncio.create_task(self._expire_permission(request_id))
        pattern_text = ", ".join(pending.patterns) if pending.patterns else "n/a"
        await self._notify_room(
            (
                f"OpenCode approval requested for `{pending.permission}` "
                f"({pattern_text}). Reply with `approve {request_id}`, "
                f"`always {request_id}`, or `reject {request_id}`."
            ),
            self._ports.turn_mentions(),
        )
        self._ports.release_turn_wait()

    async def on_question_asked(self, request: OpencodeQuestionRequest) -> None:
        request_id = request.id
        if not request_id:
            return

        pending = PendingQuestion(
            request_id=request_id,
            questions=request.questions,
        )
        _cancel_timeout(self._pending_question)
        self._pending_question = pending

        if self._config.question_mode == "auto_reject":
            await self._reject_question()
            return

        pending.timeout_task = asyncio.create_task(self._expire_question(request_id))
        await self._notify_room(
            format_question_prompt(pending.questions, request_id),
            self._ports.turn_mentions(),
        )
        self._ports.release_turn_wait()

    async def try_handle_reply(self, content: str, sender_id: str | None) -> bool:
        """Consume a room message iff it answers the pending ask.

        Returns True when the message was a permission/question reply (the
        adapter must then NOT forward it to OpenCode as a prompt).
        """
        content = content.strip()
        if not content:
            return False

        lowered = content.lower()
        # Mention the sender of THIS control message, not the turn mentions --
        # those belong to whichever turn is currently open (_begin_turn), which
        # a manual approve/reject reply does not itself start.
        mentions = [{"id": sender_id}] if sender_id else []

        if self._pending_permission:
            pending_request_id = self._pending_permission.request_id
            reply = parse_permission_reply(lowered, self._pending_permission)
            if reply:
                await self._reply_permission(reply)
                await self._notify_room(
                    f"OpenCode approval `{pending_request_id}` handled with `{reply}`.",
                    mentions,
                )
                return True

        if self._pending_question:
            pending_request_id = self._pending_question.request_id
            if lowered in {"reject", "/reject"}:
                await self._reject_question()
                await self._notify_room(
                    f"OpenCode question `{pending_request_id}` rejected.",
                    mentions,
                )
                return True

            answers = parse_question_answers(content, self._pending_question)
            if answers is None:
                await self._notify_room(
                    (
                        "OpenCode is waiting for answers. Reply with one line per "
                        "question, or `reject` to reject the question."
                    ),
                    mentions,
                )
                return True

            await self._reply_question(answers)
            await self._notify_room(
                f"OpenCode question `{pending_request_id}` answered.",
                mentions,
            )
            return True

        return False

    async def _notify_room(self, text: str, mentions: list[dict[str, str]]) -> None:
        """Post a room message best-effort.

        A send failure must never strand the turn or crash the SSE event loop:
        the platform requires at least one mention, so a sender-less turn (no
        mentions) would otherwise raise here and skip the ``release_turn_wait``
        that unblocks ``on_message``. Log and move on instead.
        """
        tools = self._ports.tools()
        if tools is None:
            return
        try:
            await tools.send_message(text, mentions=mentions)
        except Exception:
            logger.exception(
                "Failed to post approval message to room %s", self._ports.room_id
            )

    def cancel(self) -> None:
        """Drop pending state and stop its expiry timers (turn end/cleanup)."""
        _cancel_timeout(self._pending_permission)
        _cancel_timeout(self._pending_question)
        self._pending_permission = None
        self._pending_question = None

    async def _approve_own_band_tool(self, request_id: str) -> None:
        client = self._ports.client()
        session_id = self._ports.session_id()
        if client is None or not session_id:
            logger.warning(
                "Cannot auto-approve band tool permission %s for room %s",
                request_id,
                self._ports.room_id,
            )
            return
        await client.reply_permission(session_id, request_id, response="always")

    async def _reply_permission(self, reply: ApprovalReply) -> None:
        pending = self._pending_permission
        client = self._ports.client()
        if pending is None or client is None:
            return
        session_id = self._ports.session_id()
        if not session_id:
            logger.warning(
                "Cannot reply to permission %s: no session_id for room %s",
                pending.request_id,
                self._ports.room_id,
            )
            return
        _cancel_timeout(pending)
        await client.reply_permission(
            session_id,
            pending.request_id,
            response=reply,
        )
        self._pending_permission = None

    async def _reply_question(self, answers: list[list[str]]) -> None:
        pending = self._pending_question
        client = self._ports.client()
        if pending is None or client is None:
            return
        _cancel_timeout(pending)
        await client.reply_question(pending.request_id, answers=answers)
        self._pending_question = None

    async def _reject_question(self) -> None:
        pending = self._pending_question
        client = self._ports.client()
        if pending is None or client is None:
            return
        _cancel_timeout(pending)
        await client.reject_question(pending.request_id)
        self._pending_question = None

    async def _expire_permission(self, request_id: str) -> None:
        try:
            await asyncio.sleep(self._config.approval_wait_timeout_s)
        except asyncio.CancelledError:
            return

        if (
            self._pending_permission is not None
            and self._pending_permission.request_id == request_id
        ):
            await self._reply_permission(self._config.approval_timeout_reply)
            tools = self._ports.tools()
            if tools:
                await tools.send_event(
                    f"OpenCode approval `{request_id}` timed out and was handled "
                    f"with `{self._config.approval_timeout_reply}`.",
                    "error",
                )

    async def _expire_question(self, request_id: str) -> None:
        try:
            await asyncio.sleep(self._config.question_wait_timeout_s)
        except asyncio.CancelledError:
            return

        if (
            self._pending_question is not None
            and self._pending_question.request_id == request_id
        ):
            await self._reject_question()
            tools = self._ports.tools()
            if tools:
                await tools.send_event(
                    f"OpenCode question `{request_id}` timed out and was rejected.",
                    "error",
                )


def _cancel_timeout(pending: PendingPermission | PendingQuestion | None) -> None:
    if pending and pending.timeout_task:
        pending.timeout_task.cancel()
