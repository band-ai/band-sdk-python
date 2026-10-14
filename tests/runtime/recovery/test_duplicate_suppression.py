"""A live duplicate of an owned, owed or settled original adds no model turn and no reopening."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.recovery.kit import (
    TURNS,
    Ending,
    Fault,
    Harness,
    Route,
)

pytestmark = pytest.mark.looptime


@pytest.mark.parametrize(("shape", "ending"), TURNS)
class TestDuplicateSuppression:
    async def test_a_duplicate_while_the_model_runs_adds_nothing(
        self, harness: Harness, ending: Ending
    ) -> None:
        harness.adapter.gate = asyncio.Event()
        await harness.start(Route.BACKLOG)

        await harness.push(*harness.members)
        harness.adapter.gate.set()
        await harness.drain()

        assert harness.model_turns == [harness.members]
        assert harness.peer.written_since(0) == {
            m: [DeliveryStatus.PROCESSING, ending.intent] for m in harness.members
        }

    async def test_a_duplicate_of_a_settled_original_adds_nothing(
        self, harness: Harness, ending: Ending
    ) -> None:
        await harness.start(Route.BACKLOG)

        await harness.push(*harness.members)

        assert harness.model_turns == [harness.members]
        assert harness.peer.written_since(0) == {
            m: [DeliveryStatus.PROCESSING, ending.intent] for m in harness.members
        }

    @pytest.mark.parametrize("fault", list(Fault))
    async def test_a_duplicate_while_the_write_is_owed_never_reopens_or_reruns(
        self, harness: Harness, ending: Ending, fault: Fault
    ) -> None:
        harness.peer.fail_terminals(fault)
        await harness.start(Route.LIVE)

        await harness.push(*harness.members)

        assert harness.model_turns == [harness.members]
        assert harness.peer.write_kinds == {DeliveryStatus.PROCESSING, ending.intent}
        assert harness.peer.reopened == []
