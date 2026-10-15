"""Originals survive until their confirmed execution continuation finishes."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.platform.event import ReconnectedEvent
from tests.platform.settlement.kit import MISSING_ATTEMPT
from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime


class TestRetryOriginal:
    @pytest.mark.parametrize("listed", [True, False], ids=["listed", "ws-only"])
    @pytest.mark.parametrize("retries", [1, 3])
    async def test_confirmed_failures_exhaust_the_budget_in_the_same_drain(
        self, rest_peer: RestPeer, listed: bool, retries: int
    ) -> None:
        async with started_session(
            rest_peer, batching=False, retries=retries, fail_on=frozenset({"m1"})
        ) as session:
            await session.quiesce()
            await session.deliver(rest_peer.post("m1", listed=listed))
            await session.until(lambda: rest_peer.events_posted == 1)
            await session.resync()

        assert session.progress == ([("m1",)] * retries, ["m1"] * retries)
        failed_at = rest_peer.write_times(DeliveryStatus.FAILED)
        assert failed_at[-1] - failed_at[0] < session.ctx.config.idle_resync_seconds

    @pytest.mark.parametrize("parked", [False, True], ids=["uncertain", "parked"])
    async def test_a_future_retry_keeps_its_original_until_reconnect_confirms_failure(
        self, rest_peer: RestPeer, parked: bool
    ) -> None:
        rest_peer.confirm_terminal = False
        rest_peer.terminal_error = MISSING_ATTEMPT if parked else None
        async with started_session(
            rest_peer, batching=False, retries=3, fail_on=frozenset({"m1"})
        ) as session:
            await session.deliver(rest_peer.post("m1", listed=False))
            await session.reached(DeliveryStatus.PROCESSING, "m1")
            await session.resync()
            before_confirmation = session.progress
            rest_peer.confirm_terminal = True
            rest_peer.terminal_error = None
            await session.deliver(ReconnectedEvent())
            await session.until(lambda: rest_peer.events_posted == 1)

        assert (before_confirmation, session.progress) == (
            ([("m1",)], ["m1"]),
            ([("m1",)] * 3, ["m1"] * 3),
        )
        assert rest_peer.status_of("m1") is DeliveryStatus.FAILED

    async def test_failed_priority_controls_use_the_same_immediate_retry_policy(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post("head")
        async with started_session(
            rest_peer,
            batching=False,
            retries=3,
            accepting=False,
            controls=frozenset({"approve"}),
        ) as session:
            session.controls.raises.add("approve")
            await session.until(lambda: bool(session.turns.refused))
            await session.deliver(rest_peer.post("approve", listed=False))
            await session.until(lambda: rest_peer.events_posted == 1)

        assert session.controls.started == ["approve"] * 3
        assert rest_peer.status_of("approve") is DeliveryStatus.FAILED

    @pytest.mark.parametrize("parked", [False, True], ids=["uncertain", "parked"])
    async def test_failed_priority_controls_keep_their_future_retry_original(
        self, rest_peer: RestPeer, parked: bool
    ) -> None:
        async with started_session(
            rest_peer,
            batching=False,
            retries=3,
            accepting=False,
            controls=frozenset({"approve"}),
        ) as session:
            session.controls.raises.add("approve")
            await session.quiesce()
            await session.deliver(rest_peer.post("head"))
            await session.until(lambda: bool(session.turns.refused))
            rest_peer.confirm_terminal = False
            rest_peer.terminal_error = MISSING_ATTEMPT if parked else None
            await session.deliver(rest_peer.post("approve", listed=False))
            await session.until(
                lambda: bool(rest_peer.writes_of(DeliveryStatus.FAILED, "approve"))
            )
            await session.resync()
            before_confirmation = list(session.controls.started)
            rest_peer.confirm_terminal = True
            rest_peer.terminal_error = None
            await session.deliver(ReconnectedEvent())
            await session.until(lambda: rest_peer.events_posted == 1)

        assert (before_confirmation, session.controls.started) == (
            ["approve"],
            ["approve"] * 3,
        )
