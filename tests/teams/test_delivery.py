"""Distinguish definite rejection, uncertain writes and generation-fenced work."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from bandteams.evidence import Delivery
from bandteams.proposals import Dispatcher, Rejected


class TestDelivery:
    @pytest.mark.parametrize(
        "failure,expected",
        [
            (Rejected("rejected"), Delivery.FAILED),
            (OSError("response lost after commit"), Delivery.UNKNOWN),
            (None, Delivery.UNKNOWN),
        ],
    )
    async def test_dispatch_outcome_never_replays(
        self, proposals: Any, person: Any, origin: Any, failure: Any, expected: Any
    ) -> None:
        claim = proposals.stage(person, "room-one", "recipient-one", origin, "body")
        proposals.arm(claim.proposal_id, "review")
        action = origin.model_copy(update={"activity": "review"})
        attempts = []

        async def check(claim: Any) -> Any:
            return None

        async def send(claim: Any, body: Any) -> Any:
            attempts.append(body)
            if failure is not None:
                raise failure
            return "missing-accepted-message-id"

        async def receipt(claim: Any) -> Any:
            return None

        dispatcher = Dispatcher(proposals, check, send, receipt)
        try:
            await dispatcher.confirm(claim.proposal_id, 1, action)
            await dispatcher.tasks[claim.proposal_id]
            repeated = await dispatcher.confirm(claim.proposal_id, 1, action)
            assert (repeated.delivery, attempts) == (expected, ["body"])
        finally:
            await dispatcher.stop(person.alias)

    async def test_signout_while_access_check_blocked_prevents_dispatch(
        self, proposals: Any, person: Any, origin: Any
    ) -> None:
        claim = proposals.stage(person, "room-one", "recipient-one", origin, "body")
        proposals.arm(claim.proposal_id, "review")
        checking = asyncio.Event()
        attempts = []

        async def check(claim: Any) -> Any:
            checking.set()
            await asyncio.Event().wait()

        async def send(claim: Any, body: Any) -> Any:
            attempts.append(body)
            return "00000000-0000-0000-0000-000000000001"

        async def receipt(claim: Any) -> Any:
            return None

        dispatcher = Dispatcher(proposals, check, send, receipt)
        await dispatcher.confirm(
            claim.proposal_id, 1, origin.model_copy(update={"activity": "review"})
        )
        await checking.wait()
        await dispatcher.stop(person.alias)
        assert (
            proposals.claims[claim.proposal_id].delivery,
            attempts,
            dispatcher.tasks,
        ) == (Delivery.FAILED, [], {})
