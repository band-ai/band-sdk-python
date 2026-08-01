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

from band.core.backends.observing import send_non_reply_message
from band.core.protocols import AgentToolsProtocol
from band.integrations.opencode import (
    OpencodeClientProtocol,
    OpencodePermissionRequest,
    OpencodeQuestion,
    OpencodeQuestionRequest,
)

from band.adapters.opencode.config import ApprovalReply, OpencodeAdapterConfig
from band.runtime.formatters import strip_leading_mentions

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
    fail_turn: Callable[[str], None]
    is_own_band_tool: Callable[[str], bool]


@dataclass(frozen=True)
class PermissionCommand:
    """A parsed ``approve``/``always``/``reject`` room reply."""

    reply: ApprovalReply
    # None when the user named no request: resolved against the pending ask
    # when exactly one is outstanding.
    request_id: str | None


def parse_permission_reply(content: str) -> PermissionCommand | None:
    """Map a room reply (``approve <id>`` / ``always <id>`` / ``reject <id>``)
    onto the OpenCode reply vocabulary; ``None`` when it is not one of those
    commands."""
    tokens = content.split()
    if not tokens:
        return None

    command = tokens[0].lstrip("/").lower()
    trailing = tokens[1:]
    request_id = (
        None
        if not trailing or all(token.lower() == "please" for token in trailing)
        else trailing[0]
    )

    match command:
        case "approve":
            return PermissionCommand("once", request_id)
        case "always":
            return PermissionCommand("always", request_id)
        case "reject":
            return PermissionCommand("reject", request_id)
    return None


def parse_question_answers(
    content: str, pending: PendingQuestion
) -> list[list[str]] | None:
    """One answer line per question; ``None`` when too few lines arrived."""
    if not pending.questions:
        return None
    if len(pending.questions) == 1:
        answer = content.strip()
        return [[answer]] if answer else None

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) < len(pending.questions):
        return None
    return [[line] for line in lines[: len(pending.questions)]]


# The room-visible wording of an approval relay. Defined here, beside the code
# that renders it, so a consumer that has to recognize these lines (the E2E
# smoke waiting for a real permission round trip) matches one definition instead
# of re-typing the sentence.
APPROVAL_REQUESTED_PREFIX = "OpenCode approval requested for"
APPROVAL_HANDLED_TEMPLATE = "OpenCode approval `{request_id}` handled with `{reply}`."


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
        # Keyed by request id, because OpenCode can have several asks
        # outstanding at once (its own clients hold a per-session *list* of
        # pending permissions and splice by requestID). A single slot silently
        # dropped the earlier ask, whose tool call then blocked server-side
        # until the turn timed out.
        self._permissions: dict[str, PendingPermission] = {}
        self._questions: dict[str, PendingQuestion] = {}
        # Set while NO manual ask is parked on a human. Cleared only when we
        # actually forward an ask to the room and wait; set again the moment it
        # resolves. Both transitions go through the helpers below, which own
        # this event together with the human-wait clock the turn watcher reads.
        self._idle = asyncio.Event()
        self._idle.set()
        self._human_wait_total = 0.0
        self._human_wait_started: float | None = None

    def awaiting_human(self) -> bool:
        """Whether a manual permission/question is parked on a human reply."""
        return not self._idle.is_set()

    def _parked_on_human(self) -> bool:
        """Whether any ask is still waiting on a human.

        An ask has an expiry timer only when it was forwarded to the room, so
        the timer doubles as the "a human owes us a reply" marker.
        """
        return any(
            pending.timeout_task is not None
            for pending in (*self._permissions.values(), *self._questions.values())
        )

    async def wait_until_idle(self) -> None:
        """Block until no manual ask is awaiting a human reply."""
        await self._idle.wait()

    @property
    def human_wait_seconds(self) -> float:
        """Seconds this turn has spent parked on a human, including right now.

        The turn watcher adds this to ``turn_timeout_s``. Deliberation is
        bounded by the ask's own expiry timer, so charging it to the compute
        budget would abort healthy work that resumed after a slow approval.
        """
        parked = (
            0.0
            if self._human_wait_started is None
            else _clock() - self._human_wait_started
        )
        return self._human_wait_total + parked

    def _park_on_human(self) -> None:
        """Hand an ask to the room and start the human-wait clock."""
        if self._human_wait_started is None:
            self._human_wait_started = _clock()
        self._idle.clear()

    def _release_if_idle(self) -> None:
        """Release only once the LAST parked ask has resolved."""
        if not self._parked_on_human():
            self._release_from_human()

    def _release_from_human(self) -> None:
        """No ask is parked: bank the human-wait time and unblock the watcher."""
        if self._human_wait_started is not None:
            self._human_wait_total += _clock() - self._human_wait_started
            self._human_wait_started = None
        self._idle.set()

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
        self._permissions[request_id] = pending

        if self._config.approval_mode == "auto_accept":
            await self._reply_permission(pending, "once")
            return

        if self._config.approval_mode == "auto_decline":
            await self._reply_permission(pending, "reject")
            return

        pending.timeout_task = asyncio.create_task(self._expire_permission(request_id))
        self._park_on_human()
        pattern_text = ", ".join(pending.patterns) if pending.patterns else "n/a"
        await self._notify_room(
            (
                f"{APPROVAL_REQUESTED_PREFIX} `{pending.permission}` "
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
        self._questions[request_id] = pending

        if self._config.question_mode == "auto_reject":
            await self._reject_question(pending)
            return

        pending.timeout_task = asyncio.create_task(self._expire_question(request_id))
        self._park_on_human()
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
        raw = content.strip()
        if not raw:
            return False

        # A command/keyword never *is* an @mention, so skip the whole leading
        # mention block to find it (robust to several leading mentions).
        command = strip_leading_mentions(raw).strip()
        # Mention the sender of THIS control message, not the turn mentions --
        # those belong to whichever turn is currently open (_begin_turn), which
        # a manual approve/reject reply does not itself start.
        mentions = [{"id": sender_id}] if sender_id else []

        approval = parse_permission_reply(command)
        if approval and self._permissions:
            pending = self._resolve_permission(approval.request_id)
            if pending is None and approval.request_id is None:
                # Ambiguous rather than unknown: name the asks instead of
                # forwarding the reply to the model as a fresh prompt.
                await self._notify_room(self._which_permission_hint(), mentions)
                return True
            if pending is not None:
                if await self._reply_permission(pending, approval.reply):
                    await self._notify_room(
                        APPROVAL_HANDLED_TEMPLATE.format(
                            request_id=pending.request_id, reply=approval.reply
                        ),
                        mentions,
                    )
                return True

        question = self._resolve_question(command)
        if question is not None:
            if _is_question_rejection(command):
                if await self._reject_question(question):
                    await self._notify_room(
                        f"OpenCode question `{question.request_id}` rejected.",
                        mentions,
                    )
                return True

            # Free text: strip only the delivery mention so an answer that
            # legitimately begins with an @handle (naming a person) survives.
            answer = strip_leading_mentions(raw, only_first=True).strip()
            answers = parse_question_answers(answer, question)
            if answers is None:
                await self._notify_room(
                    (
                        "OpenCode is waiting for answers. Reply with one line per "
                        "question, or `reject` to reject the question."
                    ),
                    mentions,
                )
                return True

            if await self._reply_question(question, answers):
                await self._notify_room(
                    f"OpenCode question `{question.request_id}` answered.",
                    mentions,
                )
            return True

        return False

    def _resolve_permission(self, request_id: str | None) -> PendingPermission | None:
        """The ask a reply targets: the named one, else the only one pending."""
        if request_id is not None:
            return self._permissions.get(request_id)
        if len(self._permissions) == 1:
            return next(iter(self._permissions.values()))
        return None

    def _resolve_question(self, command: str) -> PendingQuestion | None:
        """The question a reply targets: the named one, else the oldest pending.

        Free text carries no request id, so it answers the oldest outstanding
        question -- the one the room was asked first.
        """
        if not self._questions:
            return None
        named = command.split()[1:] if _is_question_rejection(command) else []
        if named:
            return self._questions.get(named[0])
        return next(iter(self._questions.values()))

    def _which_permission_hint(self) -> str:
        ids = ", ".join(f"`{request_id}`" for request_id in self._permissions)
        return (
            f"Several OpenCode approvals are pending ({ids}). Reply with the "
            "request id, e.g. `approve <id>`."
        )

    async def _notify_room(self, text: str, mentions: list[dict[str, str]]) -> None:
        """Post approval plumbing text best-effort — never the turn's reply.

        Every message from here is control plane (an ask, an ack, a hint), so
        it must not count as the turn's delivery: the model's real reply still
        comes after the ask is answered, and taking these for it would silence
        the text fallback.

        A send failure must never strand the turn or crash the SSE event loop:
        the platform requires at least one mention, so a sender-less turn (no
        mentions) would otherwise raise here and skip the ``release_turn_wait``
        that unblocks ``on_message``. Log and move on instead.
        """
        tools = self._ports.tools()
        if tools is None:
            return
        try:
            await send_non_reply_message(tools, text, mentions=mentions)
        except Exception:
            logger.exception(
                "Failed to post approval message to room %s", self._ports.room_id
            )

    def cancel(self) -> None:
        """Drop pending state and stop its expiry timers (turn end/cleanup)."""
        for pending in (*self._permissions.values(), *self._questions.values()):
            _cancel_timeout(pending)
        self._permissions.clear()
        self._questions.clear()
        # No ask is parked anymore -- release any watcher waiting on us.
        self._release_from_human()

    async def _approve_own_band_tool(self, request_id: str) -> None:
        client = self._ports.client()
        session_id = self._ports.session_id()
        if client is None or not session_id:
            self._fail_request(
                "auto-approve permission",
                request_id,
            )
            return
        try:
            await client.reply_permission(session_id, request_id, response="always")
        except Exception as error:
            self._fail_request("auto-approve permission", request_id, error=error)

    async def _reply_permission(
        self, pending: PendingPermission, reply: ApprovalReply
    ) -> bool:
        client = self._ports.client()
        if client is None:
            self._fail_request("reply to permission", pending.request_id)
            return False
        session_id = self._ports.session_id()
        if not session_id:
            self._fail_request("reply to permission", pending.request_id)
            return False
        _cancel_timeout(pending)
        try:
            await client.reply_permission(
                session_id,
                pending.request_id,
                response=reply,
            )
        except Exception as error:
            self._fail_request("reply to permission", pending.request_id, error=error)
            return False
        self._forget(pending)
        return True

    async def _reply_question(
        self, pending: PendingQuestion, answers: list[list[str]]
    ) -> bool:
        client = self._ports.client()
        if client is None:
            self._fail_request("answer question", pending.request_id)
            return False
        _cancel_timeout(pending)
        try:
            await client.reply_question(pending.request_id, answers=answers)
        except Exception as error:
            self._fail_request("answer question", pending.request_id, error=error)
            return False
        self._forget(pending)
        return True

    async def _reject_question(self, pending: PendingQuestion) -> bool:
        client = self._ports.client()
        if client is None:
            self._fail_request("reject question", pending.request_id)
            return False
        _cancel_timeout(pending)
        try:
            await client.reject_question(pending.request_id)
        except Exception as error:
            self._fail_request("reject question", pending.request_id, error=error)
            return False
        self._forget(pending)
        return True

    def _forget(self, pending: PendingPermission | PendingQuestion) -> None:
        """Drop a resolved ask, releasing the watcher once none are parked."""
        registry = (
            self._permissions
            if isinstance(pending, PendingPermission)
            else self._questions
        )
        if registry.get(pending.request_id) is pending:
            del registry[pending.request_id]
        self._release_if_idle()

    async def _expire_permission(self, request_id: str) -> None:
        try:
            await asyncio.sleep(self._config.approval_wait_timeout_s)
        except asyncio.CancelledError:
            return

        pending = self._permissions.get(request_id)
        if pending is None:
            return
        if await self._reply_permission(pending, self._config.approval_timeout_reply):
            tools = self._ports.tools()
            if tools:
                await tools.send_event(
                    f"OpenCode approval `{request_id}` timed out and was handled "
                    f"with `{self._config.approval_timeout_reply}`.",
                    "error",
                )

    def _fail_request(
        self, action: str, request_id: str, *, error: Exception | None = None
    ) -> None:
        message = f"OpenCode failed to {action} `{request_id}`."
        logger.error(
            "%s Room: %s",
            message,
            self._ports.room_id,
            exc_info=error is not None,
        )
        # Abandoning a request must stop its expiry timer in the same step:
        # once popped, the entry is past cancel()'s reach, and a surviving
        # timer holds this room's state alive until the wait timeout elapses.
        _cancel_timeout(self._permissions.pop(request_id, None))
        _cancel_timeout(self._questions.pop(request_id, None))
        self._release_if_idle()
        self._ports.fail_turn(message)

    async def _expire_question(self, request_id: str) -> None:
        try:
            await asyncio.sleep(self._config.question_wait_timeout_s)
        except asyncio.CancelledError:
            return

        pending = self._questions.get(request_id)
        if pending is None:
            return
        if await self._reject_question(pending):
            tools = self._ports.tools()
            if tools:
                await tools.send_event(
                    f"OpenCode question `{request_id}` timed out and was rejected.",
                    "error",
                )


def _is_question_rejection(command: str) -> bool:
    """Whether a room reply rejects a question rather than answering it."""
    tokens = command.split()
    return bool(tokens) and tokens[0].lstrip("/").lower() == "reject"


def _clock() -> float:
    """Loop time, so the human-wait clock is immune to wall-clock changes."""
    return asyncio.get_running_loop().time()


def _cancel_timeout(pending: PendingPermission | PendingQuestion | None) -> None:
    if (
        pending
        and pending.timeout_task
        and pending.timeout_task is not asyncio.current_task()
    ):
        pending.timeout_task.cancel()
