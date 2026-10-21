"""One room's OpenCode permission/question lifecycle.

``RoomApprovals`` owns the configured auto-reply modes, the manual relay to the
room, the replies that resolve an ask, and the expiry timeouts. The adapter
reaches it only through the narrow ``ApprovalPorts`` bundle.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from band.adapters.opencode.approvals.commands import (
    PermissionCommand,
    parse_question_answers,
)
from band.adapters.opencode.approvals.humanwait import HumanWait
from band.adapters.opencode.approvals.pending import (
    PendingAsks,
    PendingPermission,
    PendingQuestion,
    PendingT,
)
from band.adapters.opencode.approvals.ports import ApprovalPorts
from band.adapters.opencode.approvals.router import (
    NamedQuestionGone,
    PermissionReply,
    QuestionAnswer,
    QuestionRejection,
    route_reply,
)
from band.adapters.opencode.approvals.wording import (
    APPROVAL_HANDLED_TEMPLATE,
    APPROVAL_NO_LONGER_PENDING_TEMPLATE,
    APPROVAL_REQUESTED_TEMPLATE,
    APPROVAL_TIMED_OUT_TEMPLATE,
    DECLINED_QUESTION_ANSWER,
    QUESTION_ANSWERED_TEMPLATE,
    QUESTION_ANSWERS_NEEDED,
    QUESTION_NO_LONGER_PENDING_TEMPLATE,
    QUESTION_REJECTED_TEMPLATE,
    QUESTION_TIMED_OUT_TEMPLATE,
    REJECTED_PERMISSION_FEEDBACK,
    format_question_prompt,
    which_dual_reject_hint,
    which_permission_hint,
    which_question_command_hint,
)
from band.adapters.opencode.config import OpencodeAdapterConfig
from band.integrations.opencode import (
    ApprovalReply,
    OpencodeClientProtocol,
    OpencodePermissionRequest,
    OpencodeQuestionRequest,
)
from band.runtime.decisions import DecisionEntry, DecisionRegistry

logger = logging.getLogger(__name__)


class ApprovalReplyError(Exception):
    """A reply could not be sent after its terminal lifecycle was handled."""


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
        self._asks = PendingAsks(known_permission_ids, known_question_ids)
        self._human = HumanWait()

    def awaiting_human(self) -> bool:
        """Whether a manual permission/question is parked on a human reply."""
        return self._asks.has_claimed() or not self._human.is_idle

    async def wait_until_idle(self) -> None:
        """Block until no manual ask is awaiting a human reply."""
        await self._human.wait_until_idle()

    @property
    def human_wait_seconds(self) -> float:
        """Seconds this turn has spent parked on a human, including right now."""
        return self._human.seconds

    def _release_if_idle(self) -> None:
        """Release only once the LAST parked ask has resolved."""
        if not self._asks.parked_on_human():
            self._human.release()

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
        if (
            registration := self._asks.permissions.register_keyed(
                pending, key=request_id
            )
        ) is None:
            return
        entry = registration.entry
        self._asks.known_permission_ids.add(request_id)
        logger.info(
            "OpenCode permission asked room=%s request=%s permission=%s mode=%s",
            self._ports.room_id,
            request_id,
            pending.permission,
            self._config.approval_mode,
        )

        match self._config.approval_mode:
            case "auto_accept":
                await self._reply_permission(pending, "once")
            case "auto_decline":
                await self._reply_permission(pending, "reject")
            case "manual":
                await self._ask_room(entry)

    async def _ask_room(self, entry: DecisionEntry[PendingPermission]) -> None:
        """Park the ask on a human: post it to the room and start its expiry."""
        pending = entry.payload
        self._asks.permissions.start_timeout(
            entry, self._config.approval_wait_timeout_s, self._expire_permission
        )
        self._human.park()
        pattern_text = ", ".join(pending.patterns) if pending.patterns else "n/a"
        await self._notify_room(
            APPROVAL_REQUESTED_TEMPLATE.format(
                permission=pending.permission,
                patterns=pattern_text,
                request_id=pending.request_id,
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
        if (
            registration := self._asks.questions.register_keyed(pending, key=request_id)
        ) is None:
            return
        entry = registration.entry
        self._asks.known_question_ids.add(request_id)
        logger.info(
            "OpenCode question asked room=%s request=%s mode=%s",
            self._ports.room_id,
            request_id,
            self._config.question_mode,
        )

        if not request.questions:
            logger.warning(
                "Declining malformed OpenCode question.asked with no questions "
                "(request_id=%s room=%s)",
                request_id,
                self._ports.room_id,
            )
            await self._reject_question(pending)
            return

        if self._config.question_mode == "auto_reject":
            await self._reject_question(pending)
            return

        self._asks.questions.start_timeout(
            entry, self._config.question_wait_timeout_s, self._expire_question
        )
        self._human.park()
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
        # Mention the sender of THIS control message, not the turn mentions --
        # those belong to whichever turn is currently open (_begin_turn), which
        # a manual approve/reject reply does not itself start.
        mentions = [{"id": sender_id}] if sender_id else []
        match route_reply(self._asks, content):
            case None:
                return False
            case PermissionReply(approval):
                await self._handle_permission_reply(approval, mentions)
            case QuestionRejection(question):
                if await self._reject_question(question):
                    await self._notify_room(
                        QUESTION_REJECTED_TEMPLATE.format(
                            request_id=question.request_id
                        ),
                        mentions,
                    )
            case QuestionAnswer(question, text):
                await self._handle_question_answer(question, text, mentions)
            case NamedQuestionGone(request_id):
                # A named question id that matched no pending question:
                # feedback, not a fresh prompt for the model.
                await self._notify_room(
                    QUESTION_NO_LONGER_PENDING_TEMPLATE.format(request_id=request_id),
                    mentions,
                )
        return True

    async def _handle_permission_reply(
        self, approval: PermissionCommand, mentions: list[dict[str, str]]
    ) -> None:
        asks = self._asks
        if (
            approval.request_id is None
            and approval.reply == "reject"
            and asks.permissions.has_unclaimed()
            and asks.questions.has_unclaimed()
        ):
            await self._notify_room(
                which_dual_reject_hint(asks.permissions, asks.questions), mentions
            )
            return
        pending = asks.resolve_permission(approval.request_id)
        if pending is None and approval.request_id is None:
            # Ambiguous rather than unknown: name the asks instead of
            # forwarding the reply to the model as a fresh prompt.
            if asks.questions.has_unclaimed() and not asks.permissions.has_unclaimed():
                hint = which_question_command_hint(asks.questions)
            else:
                hint = which_permission_hint(asks.permissions)
            await self._notify_room(hint, mentions)
            return
        if pending is not None:
            if await self._reply_permission(pending, approval.reply):
                await self._notify_room(
                    APPROVAL_HANDLED_TEMPLATE.format(
                        request_id=pending.request_id, reply=approval.reply
                    ),
                    mentions,
                )
            return
        # A named permission id that matches nothing currently pending
        # (already resolved, or another permission is pending instead):
        # feedback, not a fresh prompt for the model.
        if (
            approval.reply in ("once", "always")
            and approval.request_id in asks.questions
            and approval.request_id not in asks.permissions
            and not asks.permissions.has_unclaimed()
        ):
            await self._notify_room(
                which_question_command_hint(asks.questions), mentions
            )
        else:
            await self._notify_room(
                APPROVAL_NO_LONGER_PENDING_TEMPLATE.format(
                    request_id=approval.request_id
                ),
                mentions,
            )

    async def _handle_question_answer(
        self, question: PendingQuestion, text: str, mentions: list[dict[str, str]]
    ) -> None:
        answers = parse_question_answers(text, len(question.questions))
        if answers is None:
            await self._notify_room(QUESTION_ANSWERS_NEEDED, mentions)
            return
        if await self._reply_question(question, answers):
            await self._notify_room(
                QUESTION_ANSWERED_TEMPLATE.format(request_id=question.request_id),
                mentions,
            )

    async def _notify_room(self, text: str, mentions: list[dict[str, str]]) -> None:
        """Post an adapter notice best-effort.

        A notice, never the turn's reply: it posts on the in-flight turn's
        tools, and counting it would stop that turn relaying the model's answer.

        A send failure must never strand the turn or crash the SSE event loop:
        the platform requires at least one mention, so a sender-less turn (no
        mentions) would otherwise raise here and skip the ``release_turn_wait``
        that unblocks ``on_message``. Log and move on instead.
        """
        if (tools := self._ports.tools()) is None:
            return
        try:
            await tools.send_notice(text, mentions=mentions)
        except Exception:
            logger.exception(
                "Failed to post approval message to room %s", self._ports.room_id
            )

    def cancel(self) -> None:
        """Drop pending state and stop its expiry timers (turn end/cleanup)."""
        self._asks.cancel_all()
        # No ask is parked anymore -- release any watcher waiting on us.
        self._human.release()

    async def abandon(self) -> bool:
        """Stop a parked session after local approval state is discarded.

        Returns whether this method aborted the session, so a caller with its
        own unconditional abort afterward can skip a redundant one.
        """
        was_pending = self._asks.parked_on_human()
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
            async with self._client_reply(
                "auto-approve permission", request_id
            ) as client:
                await client.reply_permission(request_id, reply="always")
        except ApprovalReplyError:
            return

    async def _send_permission_reply(
        self, entry: DecisionEntry[PendingPermission], reply: ApprovalReply
    ) -> bool:
        """Perform the reply I/O for an already-claimed permission."""
        try:
            async with self._client_reply("reply to permission", entry.token) as client:
                await client.reply_permission(
                    entry.token,
                    reply=reply,
                    message=REJECTED_PERMISSION_FEEDBACK if reply == "reject" else None,
                )
        except ApprovalReplyError:
            return False
        logger.info(
            "OpenCode permission resolved room=%s request=%s reply=%s",
            self._ports.room_id,
            entry.token,
            reply,
        )
        self._forget(self._asks.permissions, entry)
        return True

    async def _reply_permission(
        self, pending: PendingPermission, reply: ApprovalReply
    ) -> bool:
        entry = self._claim(self._asks.permissions, pending.request_id)
        return entry is not None and await self._send_permission_reply(entry, reply)

    async def _send_question_reply(
        self, entry: DecisionEntry[PendingQuestion], answers: list[list[str]]
    ) -> bool:
        """Perform the answer I/O for an already-claimed question."""
        try:
            async with self._client_reply("answer question", entry.token) as client:
                await client.reply_question(entry.token, answers=answers)
        except ApprovalReplyError:
            return False
        logger.info(
            "OpenCode question answered room=%s request=%s",
            self._ports.room_id,
            entry.token,
        )
        self._forget(self._asks.questions, entry)
        return True

    async def _reply_question(
        self, pending: PendingQuestion, answers: list[list[str]]
    ) -> bool:
        entry = self._claim(self._asks.questions, pending.request_id)
        return entry is not None and await self._send_question_reply(entry, answers)

    async def _send_question_reject(
        self, entry: DecisionEntry[PendingQuestion]
    ) -> bool:
        """Decline an already-claimed question; see ``DECLINED_QUESTION_ANSWER``.

        A malformed question with no questions gets an empty answer list, which
        OpenCode accepts and hands back to the model like any other decline.
        """
        declined = [[DECLINED_QUESTION_ANSWER] for _ in entry.payload.questions]
        try:
            async with self._client_reply("reject question", entry.token) as client:
                await client.reply_question(entry.token, answers=declined)
        except ApprovalReplyError:
            return False
        logger.info(
            "OpenCode question rejected room=%s request=%s",
            self._ports.room_id,
            entry.token,
        )
        self._forget(self._asks.questions, entry)
        return True

    async def _reject_question(self, pending: PendingQuestion) -> bool:
        entry = self._claim(self._asks.questions, pending.request_id)
        return entry is not None and await self._send_question_reject(entry)

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
        """Failure handling for a reply sent through ``_client_reply``."""
        try:
            yield
        except Exception as error:
            await self._fail_request(action, request_id, error=error)
            raise ApprovalReplyError from error

    @asynccontextmanager
    async def _client_reply(
        self, action: str, request_id: str
    ) -> AsyncIterator[OpencodeClientProtocol]:
        if (client := self._ports.client()) is None:
            await self._fail_request(action, request_id)
            raise ApprovalReplyError
        async with self._reply_guard(action, request_id):
            yield client

    async def _expire_permission(self, entry: DecisionEntry[PendingPermission]) -> None:
        reply = self._config.approval_timeout_reply
        logger.info(
            "OpenCode permission expired room=%s request=%s reply=%s",
            self._ports.room_id,
            entry.token,
            reply,
        )
        if await self._send_permission_reply(entry, reply) and (
            tools := self._ports.tools()
        ):
            await tools.send_event(
                APPROVAL_TIMED_OUT_TEMPLATE.format(request_id=entry.token, reply=reply),
                "error",
            )

    async def _expire_question(self, entry: DecisionEntry[PendingQuestion]) -> None:
        logger.info(
            "OpenCode question expired room=%s request=%s",
            self._ports.room_id,
            entry.token,
        )
        if await self._send_question_reject(entry) and (tools := self._ports.tools()):
            await tools.send_event(
                QUESTION_TIMED_OUT_TEMPLATE.format(request_id=entry.token),
                "error",
            )
