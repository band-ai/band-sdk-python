"""Room driver for live chat-mediated coding-agent decisions."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from enum import Enum, auto

import pytest
from band_rest import ChatMessage

from band.adapters.opencode.adapter import NO_TEXT_REPLY_MESSAGE
from band.client.streaming import MessageCreatedPayload
from band.core.types import MessageType
from tests.e2e.baseline.agents import Adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.approvals import (
    ApprovalDialect,
    Notice,
    Outcome,
)
from tests.e2e.baseline.timeouts import SlowTurnBudget
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps

logger = logging.getLogger(__name__)
APPROVAL_LOG_LEVEL = (
    logging.WARNING
    if BaselineSettings().run.first_attempt_diagnostics
    else logging.INFO
)
TERMINAL_POLL_INTERVAL_S = 0.5


class TurnPhase(Enum):
    """How far a decided approval turn has got toward closing."""

    OPEN = auto()  # a notice or the closing reply hasn't streamed yet
    RUNNING = auto()  # streamed, but usage or the persisted replies lag behind
    CLOSED = auto()


@dataclass
class ReadbackAllowance:
    """The one read-back of an approved write a coding agent may follow up with."""

    commands: frozenset[str]
    used: bool = False

    def take(self, command: str | None) -> bool:
        """Whether ``command`` is the allowed read-back, spending the allowance."""
        if self.used or command not in self.commands:
            return False
        self.used = True
        return True


@dataclass
class ApprovalRoom:
    """One manual-approval agent in its own room, driven by the room's humans."""

    agent: ProvisionedAgent
    adapter_id: Adapter
    room_id: str
    capture: ReplyCapture
    dialect: ApprovalDialect
    user_ops: UserOps
    budget: SlowTurnBudget
    handled_requests: set[str] = field(default_factory=set)

    async def say(self, text: str, *, sender: UserOps | None = None) -> int:
        """Post ``text`` to the agent; return a cursor at what came before it."""
        cursor, _message_id = await self.post(text, sender=sender)
        return cursor

    async def post(
        self, text: str, *, sender: UserOps | None = None
    ) -> tuple[int, str]:
        """``say``, also returning the posted message's id for delivery barriers."""
        cursor = self.capture.messages.snapshot()
        message_id = await (sender or self.user_ops).send_message(
            self.room_id, text, mention_id=self.agent.id, mention_name=self.agent.name
        )
        return cursor, message_id

    async def decide(self, outcome: Outcome, request: re.Match[str]) -> int:
        """Answer one request as the room owner."""
        cursor = await self.say(self.dialect.reply(outcome, request))
        self.handled_requests.add(request["token"])
        logger.log(
            APPROVAL_LOG_LEVEL,
            "Approval decision adapter=%s request=%s permission=%s outcome=%s",
            self.adapter_id,
            request["token"],
            request.groupdict().get("permission", ""),
            outcome,
        )
        return cursor

    def expect_timeout(self, request: re.Match[str]) -> None:
        """Account for an unanswered request whose expiry notice is expected."""
        self.handled_requests.add(request["token"])

    async def requests(self, count: int, *, since: int = 0) -> list[re.Match[str]]:
        """The first ``count`` approval requests posted after ``since``."""
        asked = await self.capture.wait_until(
            lambda msgs: len(self.dialect.find_requests(msgs[since:])) >= count,
            deadline_s=self.budget.deadline_s,
        )
        return self.dialect.find_requests(asked[since:])[:count]

    async def shown(self, text: str, *, since: int) -> None:
        """Wait until an agent message after ``since`` shows ``text``."""
        await self.capture.wait_until(
            lambda msgs: any(text in (m.content or "") for m in msgs[since:]),
            deadline_s=self.budget.deadline_s,
        )

    async def closed(
        self,
        *notices: Notice,
        since: int,
        closing_reply: str,
        allowed_followup_commands: frozenset[str] = frozenset(),
    ) -> None:
        """Decline extra requests and wait for the decided turn to close."""
        expected_notices = list(notices)
        try:
            async with asyncio.timeout(self.budget.deadline_s):
                await self._await_close(
                    since, expected_notices, closing_reply, allowed_followup_commands
                )
        except TimeoutError:
            pytest.fail(
                f"Approval turn did not close within {self.budget.deadline_s}s: "
                f"{await self._closing_state(since, expected_notices, closing_reply)}"
            )
        logger.log(
            APPROVAL_LOG_LEVEL,
            "Approval turn closed adapter=%s requests=%s final_reply_length=%s",
            self.adapter_id,
            sorted(self.handled_requests),
            len(self.said_since(since)[-1]),
        )
        for notice in expected_notices:
            await notice.assert_shown(self.capture, self.agent.id)
        if self.adapter_id in (Adapter.CLAUDE_SDK, Adapter.OPENCODE):
            calls = await self.capture.tool_calls(sender_id=self.agent.id)
            results = await self.capture.tool_results(sender_id=self.agent.id)
            for call in calls:
                if call.name.casefold() not in ("bash", "powershell"):
                    continue
                logger.log(
                    APPROVAL_LOG_LEVEL,
                    "Approval shell call adapter=%s request=%s tool=%s arg_keys=%s",
                    self.adapter_id,
                    call.tool_call_id,
                    call.name,
                    sorted(call.args),
                )
            for result in results:
                if result.name.casefold() not in ("bash", "powershell"):
                    continue
                logger.log(
                    APPROVAL_LOG_LEVEL,
                    "Approval shell result adapter=%s request=%s tool=%s error=%s output_length=%s",
                    self.adapter_id,
                    result.tool_call_id,
                    result.name,
                    result.is_error,
                    len(result.output),
                )

    async def _await_close(
        self,
        since: int,
        notices: list[Notice],
        closing_reply: str,
        allowed_followup_commands: frozenset[str],
    ) -> None:
        readback = ReadbackAllowance(allowed_followup_commands)
        while True:
            await self._wait_for_reply_or_request(since, notices, closing_reply)
            match await self._turn_phase(since, notices, closing_reply):
                case TurnPhase.CLOSED:
                    return
                case TurnPhase.RUNNING:
                    await asyncio.sleep(TERMINAL_POLL_INTERVAL_S)
                    continue
                case TurnPhase.OPEN:
                    pass
                case list() as requests:
                    await self._decline_followups(requests, notices, readback)
            if self._opencode_missing_text_reply(since):
                logger.log(
                    APPROVAL_LOG_LEVEL,
                    "Approval no-text fallback adapter=%s requests=%s",
                    self.adapter_id,
                    sorted(self.handled_requests),
                )
                pytest.fail("OpenCode ended the approval turn without a text reply")

    async def _turn_phase(
        self, since: int, notices: list[Notice], closing_reply: str
    ) -> TurnPhase | list[re.Match[str]]:
        """Where the decided turn stands, or the follow-up requests it raised."""
        captured = self.capture.messages.since(since)
        if pending := self._unhandled_requests(captured):
            return pending
        if not self.dialect.settled(captured, *notices, closing_reply=closing_reply):
            return TurnPhase.OPEN
        if self.adapter_id is Adapter.CURSOR_ACP:
            return TurnPhase.CLOSED
        # These adapters persist usage only after their model turn ends.
        if not await self.capture.usage(sender_id=self.agent.id):
            return TurnPhase.RUNNING
        logger.log(
            APPROVAL_LOG_LEVEL,
            "Approval terminal usage adapter=%s requests=%s",
            self.adapter_id,
            sorted(self.handled_requests),
        )
        durable = await self._durable_replies()
        if pending := self._unhandled_requests(durable):
            return pending
        if self.dialect.settled(durable, *notices, closing_reply=closing_reply):
            return TurnPhase.CLOSED
        return TurnPhase.RUNNING

    async def _decline_followups(
        self,
        requests: list[re.Match[str]],
        notices: list[Notice],
        readback: ReadbackAllowance,
    ) -> None:
        """Decline every follow-up request, failing on any but one read-back."""
        unexpected: list[str] = []
        for request in requests:
            if not readback.take(self.dialect.shell_command(request)):
                unexpected.append(request["token"])
            logger.log(
                APPROVAL_LOG_LEVEL,
                "Declining follow-up approval adapter=%s request=%s permission=%s",
                self.adapter_id,
                request["token"],
                request.groupdict().get("permission", ""),
            )
            await self.decide(Outcome.DECLINE, request)
            notices.append(self.dialect.notice(Outcome.DECLINE, request))
        if unexpected:
            pytest.fail(f"Unexpected follow-up approvals: {unexpected}")

    async def _closing_state(
        self, since: int, notices: list[Notice], closing_reply: str
    ) -> str:
        """Which close condition is missing, without quoting any message text."""
        captured = self.capture.messages.since(since)
        said = self.said_since(since)
        settled = self.dialect.settled(captured, *notices, closing_reply=closing_reply)
        durable_settled = self.dialect.settled(
            await self._durable_replies(), *notices, closing_reply=closing_reply
        )
        usage = await self.capture.usage(sender_id=self.agent.id)
        # Event notices only show up in a post-turn read; assert_shown checks those.
        text_notices_shown = [
            notice.streamed_in(said)
            for notice in notices
            if notice.message_type == MessageType.TEXT
        ]
        return (
            f"agent_messages={len(said)} "
            f"text_notices_shown={text_notices_shown} "
            f"closing_word_said={any(closing_reply in content for content in said)} "
            f"settled={settled} "
            f"durable_settled={durable_settled} "
            f"unhandled_requests={len(self._unhandled_requests(captured))} "
            f"usage_recorded={bool(usage)}"
        )

    async def _durable_replies(self) -> list[ChatMessage]:
        """The agent's persisted chat messages in this room."""
        return [
            message
            for message in await self.user_ops.list_messages(
                self.room_id, message_type=MessageType.TEXT
            )
            if message.sender_id == self.agent.id
        ]

    def unanswered_requests(self, *, since: int) -> list[re.Match[str]]:
        """Captured requests after ``since`` with no decision or expected timeout."""
        return self._unhandled_requests(self.capture.messages.since(since))

    def _unhandled_requests(
        self, messages: list[MessageCreatedPayload | ChatMessage]
    ) -> list[re.Match[str]]:
        return [
            request
            for request in self.dialect.find_requests(messages)
            if request["token"] not in self.handled_requests
        ]

    async def _wait_for_reply_or_request(
        self, since: int, notices: list[Notice], closing_reply: str
    ) -> None:
        await self.capture.wait_until(
            lambda _msgs: (
                self.dialect.settled(
                    self.capture.messages.since(since),
                    *notices,
                    closing_reply=closing_reply,
                )
                or bool(self._unhandled_requests(self.capture.messages.since(since)))
                or self._opencode_missing_text_reply(since)
            ),
            deadline_s=self.budget.deadline_s,
        )

    def _opencode_missing_text_reply(self, since: int) -> bool:
        return self.adapter_id is Adapter.OPENCODE and any(
            NO_TEXT_REPLY_MESSAGE in reply for reply in self.said_since(since)
        )

    def said_since(self, since: int) -> list[str]:
        return [m.content or "" for m in self.capture.messages.since(since)]
