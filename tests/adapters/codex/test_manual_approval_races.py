"""Codex adapter: races between a room reply and the approval wait's own timeout."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.codex import (
    NO_APPROVALS_TO_RESOLVE_MESSAGE,
    CodexCommand,
)
from band.testing import events_of_type
from tests.adapters.codex.kit import CodexApprovalRoom


class TestManualApprovalRaces:
    """Races between a room reply and the approval wait's own timeout."""

    @pytest.mark.asyncio
    async def test_a_reply_that_claims_while_the_prompt_send_fails_still_wins(
        self, approval_room: Callable[..., Awaitable[CodexApprovalRoom]]
    ) -> None:
        """The approval id is visible in the task event before the prompt
        send; a reply claiming it while that send fails owns the answer."""
        room = await approval_room()
        failing_prompt = room.tools.hold_message(
            "Approval requested", error=RuntimeError("network down")
        )
        decision = room.ask("approval-xyz", request_id=1)

        async with failing_prompt:
            await room.reply(CodexCommand.APPROVE)

        assert await decision == "accept"
        assert events_of_type(room.tools, "error") == []

    @pytest.mark.asyncio
    @pytest.mark.looptime
    async def test_a_late_reply_during_the_timeout_notice_is_not_reported_as_resolved(
        self, approval_room: Callable[..., Awaitable[CodexApprovalRoom]]
    ) -> None:
        """A reply landing while the timeout notice is still being sent must
        not be told "resolved" for a decision that already timed out."""
        room = await approval_room(approval_wait_timeout_s=300)
        timeout_notice = room.tools.hold_message("timed out")
        decision = room.ask("approval-xyz", request_id=1)

        async with timeout_notice:
            assert await room.reply(CodexCommand.APPROVE, "approval-xyz") == (
                NO_APPROVALS_TO_RESOLVE_MESSAGE
            )

        assert await decision == "decline"  # approval_timeout_decision

    @pytest.mark.asyncio
    async def test_redelivered_approvals_never_hang_evict_or_override_a_claim(
        self, approval_room: Callable[..., Awaitable[CodexApprovalRoom]]
    ) -> None:
        """At capacity, with one approval claimed by a reply mid-resolution:
        a second reply to it is told it's no longer pending; Codex re-sending
        it declines at once without waiting; re-sending the open one
        supersedes it (declining its asker) without evicting anything; and
        both final answers stand."""
        room = await approval_room(max_pending_approvals_per_room=2)
        async with room.prompted("approval-a", "approval-b"):
            claimed_ask = room.ask("approval-a", request_id=1)
            superseded = room.ask("approval-b", request_id=2)
        claimed = room.pending.try_claim("approval-a")
        assert claimed is not None

        assert await room.reply(CodexCommand.APPROVE, "approval-a") == (
            "Approval `approval-a` is no longer pending."
        )
        refused = room.ask("approval-a", request_id=3)
        redelivery = room.ask("approval-b", request_id=4)

        assert await refused == "decline"
        assert await superseded == "decline"
        assert list(room.pending) == ["approval-a", "approval-b"]
        await room.reply(CodexCommand.DECLINE, "approval-b")
        claimed.payload.future.set_result("accept")
        assert await redelivery == "decline"
        assert await claimed_ask == "accept"
