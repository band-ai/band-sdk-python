"""A terminal write the platform answers with 204 keeps its intent without an error."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.platform.settlement.kit import Reply
from tests.runtime.delivery.kit import RestPeer, error_causes, started_session

pytestmark = pytest.mark.looptime

ORIGINAL = "original"
PLATFORM_STOPPED = Reply(204)


class TestStoppedTerminalWrite:
    @pytest.mark.parametrize(
        ("fail_on", "intent", "logged"),
        [
            pytest.param(
                frozenset({ORIGINAL}),
                DeliveryStatus.FAILED,
                {RuntimeError},  # the model's own failure
                id="the-model-fails",
            ),
            pytest.param(
                frozenset(), DeliveryStatus.PROCESSED, set(), id="the-model-completes"
            ),
        ],
    )
    async def test_the_intent_is_retained_and_written_once_the_platform_resumes(
        self,
        rest_peer: RestPeer,
        caplog: pytest.LogCaptureFixture,
        fail_on: frozenset[str],
        intent: DeliveryStatus,
        logged: set[type[BaseException]],
    ) -> None:
        rest_peer.post(ORIGINAL)
        rest_peer.terminal_error = PLATFORM_STOPPED

        async with started_session(rest_peer, fail_on=fail_on) as session:
            await session.quiesce()
            causes = error_causes(caplog.records)
            rest_peer.terminal_error = None
            await session.resync()

        assert (causes, session.turns.ids, rest_peer.status_of(ORIGINAL)) == (
            logged,
            [(ORIGINAL,)],
            intent,
        )
