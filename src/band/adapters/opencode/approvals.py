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
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

from band.adapters.opencode.config import ApprovalReply, OpencodeAdapterConfig
from band.core.protocols import AgentToolsProtocol
from band.integrations.opencode import (
    OpencodeClientProtocol,
    OpencodePermissionRequest,
    OpencodeQuestion,
    OpencodeQuestionRequest,
)
from band.runtime.decisions import DecisionEntry, DecisionRegistry
from band.runtime.formatters import strip_leading_mentions

logger = logging.getLogger(__name__)


@dataclass
class PendingPermission:
    request_id: str
    permission: str
    patterns: list[str]


@dataclass
class PendingQuestion:
    request_id: str
    questions: list[OpencodeQuestion]


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
    abort_session: Callable[[], Awaitable[None]]
    is_own_band_tool: Callable[[str], bool]


class PermissionReplyWord(StrEnum):
    """The room words that answer a permission ask."""

    APPROVE = "approve"
    ALWAYS = "always"
    REJECT = "reject"


# Keyed for lookup by the raw room word.
_PERMISSION_REPLIES: dict[str, ApprovalReply] = {
    PermissionReplyWord.APPROVE: "once",
    PermissionReplyWord.ALWAYS: "always",
    PermissionReplyWord.REJECT: "reject",
}


@dataclass(frozen=True)
class PermissionCommand:
    """A parsed ``approve``/``always``/``reject`` room reply."""

    reply: ApprovalReply
    # None when the user named no request: resolved against the pending ask
    # when exactly one is outstanding.
    request_id: str | None


class ApprovalReplyError(Exception):
    """A reply could not be sent after its terminal lifecycle was handled."""


def parse_permission_reply(content: str) -> PermissionCommand | None:
    """Map a room reply (``approve <id>`` / ``always <id>`` / ``reject <id>``)
    onto the OpenCode reply vocabulary; ``None`` when it is not one of those
    commands."""
    if not (tokens := content.split()):
        return None

    command = tokens[0].lstrip("/").lower()
    trailing = tokens[1:]
    request_id = (
        None
        if not trailing or all(token.lower() == "please" for token in trailing)
        else trailing[0]
    )

    if (reply := _PERMISSION_REPLIES.get(command)) is None:
        return None
    return PermissionCommand(reply, request_id)


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
APPROVAL_REQUESTED_TEMPLATE = (
    "OpenCode approval requested for `{permission}` ({patterns}). Reply with "
    f"`{PermissionReplyWord.APPROVE} {{request_id}}`, "
    f"`{PermissionReplyWord.ALWAYS} {{request_id}}`, or "
    f"`{PermissionReplyWord.REJECT} {{request_id}}`."
)
APPROVAL_HANDLED_TEMPLATE = "OpenCode approval `{request_id}` handled with `{reply}`."
APPROVAL_TIMED_OUT_TEMPLATE = (
    "OpenCode approval `{request_id}` timed out and was handled with `{reply}`."
)
APPROVAL_NO_LONGER_PENDING_TEMPLATE = (
    "OpenCode approval `{request_id}` is no longer pending."
)
QUESTION_NO_LONGER_PENDING_TEMPLATE = (
    "OpenCode question `{request_id}` is no longer pending."
)


def format_question_prompt(questions: list[OpencodeQuestion], request_id: str) -> str:
    prompt_lines = [f"OpenCode asked question `{request_id}`:"]
    for index, question in enumerate(questions, start=1):
        prompt_lines.append(f"{index}. {question.question}")
    prompt_lines.append("Reply with one line per question, or `reject`.")
    return "\n".join(prompt_lines)


PendingT = TypeVar("PendingT", PendingPermission, PendingQuestion)


class RoomApprovals:
    """Owns one room's pending permission/question state and its lifecycle."""

    def __init__(
        self,
        config: OpencodeAdapterConfig,
        ports: ApprovalPorts,
        *,
        known_permission_ids: set[str] | None = None,
        known_question_ids: set[str] | None = None,
    ) -> None:
        self._config = config
        self._ports = ports
        # Keyed by request id, because OpenCode can have several asks
        # outstanding at once (its own clients hold a per-session *list* of
        # pending permissions and splice by requestID). A single slot silently
        # dropped the earlier ask, whose tool call then blocked server-side
        # until the turn timed out.
        self._permissions: DecisionRegistry[PendingPermission] = DecisionRegistry()
        self._questions: DecisionRegistry[PendingQuestion] = DecisionRegistry()
        # Every id ever asked, so a reply naming one still gets "no longer
        # pending" feedback instead of being forwarded as a fresh prompt --
        # the caller may pass a room-scoped set that outlives this instance.
        self._known_permission_ids = (
            known_permission_ids if known_permission_ids is not None else set()
        )
        self._known_question_ids = (
            known_question_ids if known_question_ids is not None else set()
        )
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
        if self._permissions.has_claimed() or self._questions.has_claimed():
            return True
        return not self._idle.is_set()

    def _parked_on_human(self) -> bool:
        """Whether any ask is still waiting on a human.

        An ask counts as parked until it's claimed -- claiming always cancels
        its expiry timer, so "still parked" and "still unclaimed" coincide.
        """
        return bool(
            self._permissions.unclaimed_count() or self._questions.unclaimed_count()
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

    def _claim(
        self, registry: DecisionRegistry[PendingT], request_id: str
    ) -> DecisionEntry[PendingT] | None:
        """Claim an entry, releasing the human-wait clock if it was the last
        one parked. ``None`` when it's already claimed or gone -- a
        redelivery, or another reply already in flight for it -- either way
        the caller must not touch it."""
        if (entry := registry.try_claim(request_id)) is not None:
            self._release_if_idle()
        return entry

    def _forget(
        self, registry: DecisionRegistry[PendingT], entry: DecisionEntry[PendingT]
    ) -> None:
        """Drop an answered ask, releasing the watcher once none are parked."""
        registry.forget(entry)
        self._release_if_idle()

    async def on_permission_asked(self, request: OpencodePermissionRequest) -> None:
        if not (request_id := request.id):
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
        if (entry := self._permissions.register(pending, key=request_id)) is None:
            return
        self._known_permission_ids.add(request_id)

        if self._config.approval_mode == "auto_accept":
            await self._reply_permission(pending, "once")
            return

        if self._config.approval_mode == "auto_decline":
            await self._reply_permission(pending, "reject")
            return

        self._permissions.start_timeout(
            entry, self._config.approval_wait_timeout_s, self._expire_permission
        )
        self._park_on_human()
        pattern_text = ", ".join(pending.patterns) if pending.patterns else "n/a"
        await self._notify_room(
            APPROVAL_REQUESTED_TEMPLATE.format(
                permission=pending.permission,
                patterns=pattern_text,
                request_id=request_id,
            ),
            self._ports.turn_mentions(),
        )
        self._ports.release_turn_wait()

    async def on_question_asked(self, request: OpencodeQuestionRequest) -> None:
        if not (request_id := request.id):
            logger.warning(
                "Ignoring malformed OpenCode question.asked with no request id "
                "(request_id=%s room=%s)",
                request_id,
                self._ports.room_id,
            )
            return

        pending = PendingQuestion(
            request_id=request_id,
            questions=request.questions,
        )
        if (entry := self._questions.register(pending, key=request_id)) is None:
            return
        self._known_question_ids.add(request_id)

        if not request.questions:
            logger.warning(
                "Rejecting malformed OpenCode question.asked with no questions "
                "(request_id=%s room=%s)",
                request_id,
                self._ports.room_id,
            )
            await self._reject_question(pending)
            return

        if self._config.question_mode == "auto_reject":
            await self._reject_question(pending)
            return

        self._questions.start_timeout(
            entry, self._config.question_wait_timeout_s, self._expire_question
        )
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
        if not (raw := content.strip()):
            return False

        # A command/keyword never *is* an @mention, so skip the whole leading
        # mention block to find it (robust to several leading mentions).
        command = strip_leading_mentions(raw).strip()
        # Mention the sender of THIS control message, not the turn mentions --
        # those belong to whichever turn is currently open (_begin_turn), which
        # a manual approve/reject reply does not itself start.
        mentions = [{"id": sender_id}] if sender_id else []

        approval = parse_permission_reply(command)
        if approval and self._permission_command_applies(approval):
            if (
                approval.request_id is None
                and approval.reply == "reject"
                and self._permissions.unclaimed_count()
                and self._questions.unclaimed_count()
            ):
                await self._notify_room(self._which_dual_reject_hint(), mentions)
                return True
            pending = self._resolve_permission(approval.request_id)
            if pending is None and approval.request_id is None:
                # Ambiguous rather than unknown: name the asks instead of
                # forwarding the reply to the model as a fresh prompt.
                if (
                    self._questions.unclaimed_count()
                    and not self._permissions.unclaimed_count()
                ):
                    await self._notify_room(
                        self._which_question_command_hint(), mentions
                    )
                else:
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
            # A named permission id that matches nothing currently pending
            # (already resolved, or another permission is pending instead):
            # feedback, not a fresh prompt for the model.
            if (
                approval.reply in ("once", "always")
                and approval.request_id in self._questions
                and approval.request_id not in self._permissions
                and not self._permissions.unclaimed_count()
            ):
                await self._notify_room(self._which_question_command_hint(), mentions)
            else:
                await self._notify_room(
                    APPROVAL_NO_LONGER_PENDING_TEMPLATE.format(
                        request_id=approval.request_id
                    ),
                    mentions,
                )
            return True

        if (question := self._resolve_question(command)) is not None:
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
            if (answers := parse_question_answers(answer, question)) is None:
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

        if (
            approval is not None
            and approval.reply == "reject"
            and approval.request_id is not None
        ):
            request_id = approval.request_id
            if self._questions or request_id in self._known_question_ids:
                # A named question id that matched no pending question above:
                # feedback, not a fresh prompt for the model.
                await self._notify_room(
                    QUESTION_NO_LONGER_PENDING_TEMPLATE.format(request_id=request_id),
                    mentions,
                )
                return True

        return False

    def _permission_command_applies(self, approval: PermissionCommand) -> bool:
        """Whether a parsed approve/always/reject targets a permission.

        ``reject <id>`` is also the question-rejection grammar. A named id
        that is a live or known question (and not a permission) must fall
        through so ``_resolve_question`` / ``_reject_question`` can run.
        ``approve`` / ``always`` are not shared; those stay on this branch
        so they are not submitted as free-text question answers.
        """
        named = approval.request_id
        if (
            named is not None
            and approval.reply in ("once", "always")
            and named not in self._permissions
            and named not in self._known_permission_ids
            and named not in self._known_question_ids
            and self._questions.unclaimed_count()
        ):
            return False
        if (
            named is not None
            and approval.reply == "reject"
            and named in self._questions
        ):
            return False
        if approval.reply in ("once", "always"):
            return self._parked_on_human() or (
                named is not None
                and (
                    named in self._known_permission_ids
                    or named in self._known_question_ids
                )
            )
        return bool(self._permissions.unclaimed_count()) or (
            named is not None and named in self._known_permission_ids
        )

    def _resolve_permission(self, request_id: str | None) -> PendingPermission | None:
        """The ask a reply targets: the named one, else the only one still
        awaiting an answer."""
        if request_id is not None:
            return self._permissions.get(request_id)
        match self._permissions.unclaimed():
            case [only]:
                return only.payload
            case _:
                return None

    def _resolve_question(self, command: str) -> PendingQuestion | None:
        """The question a reply targets: the named one, else the oldest pending.

        Free text carries no request id, so it answers the oldest question
        still awaiting an answer -- not one whose reply is already in flight.
        """
        if _is_question_rejection(command) and (named := command.split()[1:]):
            return self._questions.get(named[0])
        oldest = self._questions.oldest_unclaimed()
        return None if oldest is None else oldest.payload

    def _which_permission_hint(self) -> str:
        ids = _format_ids(self._permissions)
        return (
            f"Several OpenCode approvals are pending ({ids}). Reply with the "
            f"request id, e.g. `{PermissionReplyWord.APPROVE} <id>`."
        )

    def _which_question_command_hint(self) -> str:
        ids = _format_ids(self._questions)
        return (
            f"OpenCode is waiting for question answers ({ids}). Reply with your "
            f"answer or `{PermissionReplyWord.REJECT} <id>` — not "
            f"`{PermissionReplyWord.APPROVE}`/`{PermissionReplyWord.ALWAYS}`."
        )

    def _which_dual_reject_hint(self) -> str:
        perm_ids = _format_ids(self._permissions)
        question_ids = _format_ids(self._questions)
        return (
            "Both an approval and a question are pending "
            f"({perm_ids}; {question_ids}). Reply with "
            f"`{PermissionReplyWord.REJECT} <id>` naming "
            "which ask to reject."
        )

    async def _notify_room(self, text: str, mentions: list[dict[str, str]]) -> None:
        """Post a room message best-effort.

        A send failure must never strand the turn or crash the SSE event loop:
        the platform requires at least one mention, so a sender-less turn (no
        mentions) would otherwise raise here and skip the ``release_turn_wait``
        that unblocks ``on_message``. Log and move on instead.
        """
        if (tools := self._ports.tools()) is None:
            return
        try:
            await tools.send_message(text, mentions=mentions)
        except Exception:
            logger.exception(
                "Failed to post approval message to room %s", self._ports.room_id
            )

    def cancel(self) -> None:
        """Drop pending state and stop its expiry timers (turn end/cleanup)."""
        self._permissions.cancel_all()
        self._questions.cancel_all()
        # No ask is parked anymore -- release any watcher waiting on us.
        self._release_from_human()

    async def abandon(self) -> bool:
        """Stop a parked session after local approval state is discarded.

        Returns whether this method aborted the session, so a caller with its
        own unconditional abort afterward can skip a redundant one.
        """
        was_pending = self._parked_on_human()
        self.cancel()
        if was_pending:
            logger.info(
                "OpenCode turn: abandon pending approvals room=%s",
                self._ports.room_id,
            )
            await self._ports.abort_session()
        return was_pending

    async def _approve_own_band_tool(self, request_id: str) -> None:
        try:
            async with self._permission_reply(
                "auto-approve permission", request_id
            ) as (
                client,
                session_id,
            ):
                await client.reply_permission(session_id, request_id, response="always")
        except ApprovalReplyError:
            return

    async def _send_permission_reply(
        self, entry: DecisionEntry[PendingPermission], reply: ApprovalReply
    ) -> bool:
        """Perform the reply I/O for an already-claimed permission."""
        try:
            async with self._permission_reply("reply to permission", entry.token) as (
                client,
                session_id,
            ):
                await client.reply_permission(session_id, entry.token, response=reply)
        except ApprovalReplyError:
            return False
        self._forget(self._permissions, entry)
        return True

    async def _reply_permission(
        self, pending: PendingPermission, reply: ApprovalReply
    ) -> bool:
        if (entry := self._claim(self._permissions, pending.request_id)) is None:
            return False
        return await self._send_permission_reply(entry, reply)

    async def _send_question_reply(
        self, entry: DecisionEntry[PendingQuestion], answers: list[list[str]]
    ) -> bool:
        """Perform the answer I/O for an already-claimed question."""
        try:
            async with self._question_reply("answer question", entry.token) as client:
                await client.reply_question(entry.token, answers=answers)
        except ApprovalReplyError:
            return False
        self._forget(self._questions, entry)
        return True

    async def _reply_question(
        self, pending: PendingQuestion, answers: list[list[str]]
    ) -> bool:
        if (entry := self._claim(self._questions, pending.request_id)) is None:
            return False
        return await self._send_question_reply(entry, answers)

    async def _send_question_reject(
        self, entry: DecisionEntry[PendingQuestion]
    ) -> bool:
        """Perform the reject I/O for an already-claimed question."""
        try:
            async with self._question_reply("reject question", entry.token) as client:
                await client.reject_question(entry.token)
        except ApprovalReplyError:
            return False
        self._forget(self._questions, entry)
        return True

    async def _reject_question(self, pending: PendingQuestion) -> bool:
        if (entry := self._claim(self._questions, pending.request_id)) is None:
            return False
        return await self._send_question_reject(entry)

    async def _fail_request(
        self, action: str, request_id: str, *, error: Exception | None = None
    ) -> None:
        message = f"OpenCode failed to {action} `{request_id}`."
        logger.error(
            "%s Room: %s",
            message,
            self._ports.room_id,
            exc_info=error is not None,
        )
        # abort_session kills the whole session, so no sibling ask can be
        # answered either -- drop them all, timers included.
        self.cancel()
        self._ports.fail_turn(message)
        await self._ports.abort_session()

    @asynccontextmanager
    async def _reply_guard(self, action: str, request_id: str) -> AsyncIterator[None]:
        """Shared failure handling for the two reply context managers below."""
        try:
            yield
        except Exception as error:
            await self._fail_request(action, request_id, error=error)
            raise ApprovalReplyError from error

    @asynccontextmanager
    async def _permission_reply(
        self, action: str, request_id: str
    ) -> AsyncIterator[tuple[OpencodeClientProtocol, str]]:
        client = self._ports.client()
        session_id = self._ports.session_id()
        if client is None or not session_id:
            await self._fail_request(action, request_id)
            raise ApprovalReplyError
        async with self._reply_guard(action, request_id):
            yield client, session_id

    @asynccontextmanager
    async def _question_reply(
        self, action: str, request_id: str
    ) -> AsyncIterator[OpencodeClientProtocol]:
        if (client := self._ports.client()) is None:
            await self._fail_request(action, request_id)
            raise ApprovalReplyError
        async with self._reply_guard(action, request_id):
            yield client

    async def _expire_permission(self, entry: DecisionEntry[PendingPermission]) -> None:
        reply = self._config.approval_timeout_reply
        if await self._send_permission_reply(entry, reply) and (
            tools := self._ports.tools()
        ):
            await tools.send_event(
                APPROVAL_TIMED_OUT_TEMPLATE.format(request_id=entry.token, reply=reply),
                "error",
            )

    async def _expire_question(self, entry: DecisionEntry[PendingQuestion]) -> None:
        if await self._send_question_reject(entry) and (tools := self._ports.tools()):
            await tools.send_event(
                f"OpenCode question `{entry.token}` timed out and was rejected.",
                "error",
            )


def _format_ids(registry: DecisionRegistry[Any]) -> str:
    """The asks still awaiting an answer, as a hint's id list."""
    return ", ".join(f"`{entry.token}`" for entry in registry.unclaimed())


def _is_question_rejection(command: str) -> bool:
    """Whether a room reply rejects a question rather than answering it."""
    tokens = command.split()
    return bool(tokens) and tokens[0].lstrip("/").lower() == PermissionReplyWord.REJECT


def _clock() -> float:
    """Loop time, so the human-wait clock is immune to wall-clock changes."""
    return asyncio.get_running_loop().time()
