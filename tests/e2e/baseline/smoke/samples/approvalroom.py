"""Room driver for live chat-mediated coding-agent decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tests.e2e.baseline.smoke.samples.approvals import ApprovalDialect, Notice
from tests.e2e.baseline.timeouts import SlowTurnBudget
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps


@dataclass
class ApprovalRoom:
    """One manual-decision agent in a room driven by its human participants."""

    agent: ProvisionedAgent
    room_id: str
    capture: ReplyCapture
    dialect: ApprovalDialect
    user_ops: UserOps
    budget: SlowTurnBudget

    async def say(self, text: str, *, sender: UserOps | None = None) -> int:
        """Post to the agent and return the preceding capture cursor."""
        cursor = self.capture.messages.snapshot()
        await (sender or self.user_ops).send_message(
            self.room_id, text, mention_id=self.agent.id, mention_name=self.agent.name
        )
        return cursor

    async def requests(self, count: int, *, since: int = 0) -> list[re.Match[str]]:
        """Return the first distinct permission requests after the cursor."""
        try:
            asked = await self.capture.wait_until(
                lambda msgs: len(self.dialect.find_requests(msgs[since:])) >= count,
                deadline_s=self.budget.deadline_s,
            )
        except TimeoutError as error:
            raise TimeoutError(
                f"{error}; room messages: {self.said_since(since)}"
            ) from error
        return self.dialect.find_requests(asked[since:])[:count]

    async def shown(self, text: str, *, since: int) -> None:
        """Wait for an agent message containing text after the cursor."""
        await self.capture.wait_until(
            lambda msgs: any(text in (m.content or "") for m in msgs[since:]),
            deadline_s=self.budget.deadline_s,
        )

    async def closed(self, *notices: Notice, since: int) -> None:
        """Wait for decision notices and the closing reply."""
        await self.capture.wait_until(
            lambda _msgs: self.dialect.settled(
                self.capture.messages.since(since), *notices
            ),
            deadline_s=self.budget.deadline_s,
        )
        for notice in notices:
            await notice.assert_shown(self.capture, self.agent.id)

    def said_since(self, since: int) -> list[str]:
        return [m.content or "" for m in self.capture.messages.since(since)]
