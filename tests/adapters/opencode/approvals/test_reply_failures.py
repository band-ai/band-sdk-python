"""A failed reply or approval post fails only the affected turn."""

from __future__ import annotations

from typing import cast

from band.adapters.opencode import OpencodeAdapterConfig
from band.integrations.opencode import (
    OpencodeClientProtocol,
)
from band.testing import FakeAgentTools
from tests.adapters.opencode.approvals.kit import (
    FailingReplyClient,
    make_room_approvals,
)
from tests.adapters.opencode.helpers import (
    AskFactory,
    FakeOpencodeClient,
    RaisingSendTools,
)


class TestReplyFailures:
    async def test_notify_room_send_failure_does_not_strand_turn(
        self, asks: AskFactory
    ) -> None:
        """The approval-request post is best-effort: if send_message raises, the
        failure is swallowed so the turn still unblocks (``release_turn_wait``
        runs) instead of stranding the paused session or crashing the event loop."""
        released: list[bool] = []
        approvals = make_room_approvals(
            cast(OpencodeClientProtocol, FakeOpencodeClient()),
            tools=RaisingSendTools(),
            release_turn_wait=lambda: released.append(True),
        )

        await approvals.on_permission_asked(asks.permission("req-1"))

        assert released == [True]

    async def test_failed_auto_replies_fail_only_the_affected_turn(
        self,
        asks: AskFactory,
    ) -> None:
        failures: list[str] = []
        aborted: list[bool] = []

        async def record_abort() -> None:
            aborted.append(True)

        client = FailingReplyClient()
        approvals = make_room_approvals(
            client,
            fail_turn=failures.append,
            abort_session=record_abort,
            config=OpencodeAdapterConfig(
                approval_mode="auto_accept",
                question_mode="auto_reject",
            ),
        )

        await approvals.on_permission_asked(asks.permission("permission-1"))
        await approvals.on_question_asked(asks.question("question-1"))

        assert failures == [
            "OpenCode failed to reply to permission `permission-1`.",
            "OpenCode failed to reject question `question-1`.",
        ]
        assert aborted == [True, True]

    async def test_failed_reply_cancels_sibling_asks(self, asks: AskFactory) -> None:
        """A failed permission reply aborts the session, so leftover siblings
        must be dropped -- they can never be answered."""
        aborted: list[bool] = []

        async def record_abort() -> None:
            aborted.append(True)

        client = FailingReplyClient()
        tools = FakeAgentTools()
        approvals = make_room_approvals(
            client,
            tools=tools,
            abort_session=record_abort,
        )
        await approvals.on_permission_asked(asks.permission("perm-1"))
        await approvals.on_permission_asked(
            asks.permission("perm-2", permission="edit")
        )

        assert await approvals.try_handle_reply("approve perm-1", "user-1")
        assert aborted == [True]
        assert not approvals.awaiting_human()
        assert await approvals.try_handle_reply("approve perm-2", "user-1")
        assert "no longer pending" in tools.messages_sent[-1]["content"]
