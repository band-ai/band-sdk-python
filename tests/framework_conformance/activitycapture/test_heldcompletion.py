"""A held activity operation completes only when the test releases it."""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.baseline.toolkit.activitywork import (
    ActivityLookupInput,
    ActivityWork,
    Operation,
)
from tests.framework_conformance.activitycapture.lookup import HeldLookupPeer


class TestHeldCompletion:
    async def test_a_released_call_completes_with_its_result(
        self, held_lookup: tuple[ActivityWork, HeldLookupPeer]
    ) -> None:
        work, peer = held_lookup
        call = asyncio.create_task(
            work.execute(ActivityLookupInput(key=Operation.C, private_note=work.note))
        )
        peer.release.set()
        await work.entered(Operation.C)
        work[Operation.C].release.set()

        assert await work.completed(Operation.C) == await call

    async def test_a_call_cancelled_while_held_fails_completion_by_name(
        self, held_lookup: tuple[ActivityWork, HeldLookupPeer]
    ) -> None:
        work, peer = held_lookup
        call = asyncio.create_task(
            work.execute(ActivityLookupInput(key=Operation.C, private_note=work.note))
        )
        peer.release.set()
        await work.entered(Operation.C)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

        with pytest.raises(AssertionError, match=f"Operation {Operation.C.value} "):
            await work.completed(Operation.C)
