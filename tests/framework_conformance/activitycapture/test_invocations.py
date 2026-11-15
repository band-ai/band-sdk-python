"""One activity operation admits one invocation before fetching its result."""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.baseline.toolkit.activitywork import (
    LOOKUP_PATH,
    ActivityLookupInput,
    ActivityWork,
    Operation,
)
from tests.framework_conformance.activitycapture.lookup import HeldLookupPeer


class TestActivityInvocations:
    async def test_duplicate_during_fetch_cannot_share_completion_barriers(
        self, held_lookup: tuple[ActivityWork, HeldLookupPeer]
    ) -> None:
        work, peer = held_lookup
        request = ActivityLookupInput(key=Operation.A, private_note=work.note)
        first = asyncio.create_task(work.execute(request))
        try:
            await peer.entered.wait()
            with pytest.raises(ValueError):
                await work.execute(request)
            assert peer.requests == [LOOKUP_PATH]
            assert not work[Operation.A].settled.is_set()
            peer.release.set()
            await work.entered(Operation.A)
            work[Operation.A].release.set()
            assert await first == work[Operation.A].result
            assert work[Operation.A].settled.is_set()
        finally:
            peer.release.set()
            work[Operation.A].release.set()
            await first

    async def test_cleanup_joins_an_invocation_still_fetching_its_result(
        self, held_lookup: tuple[ActivityWork, HeldLookupPeer]
    ) -> None:
        work, peer = held_lookup
        closing, closed = asyncio.Event(), asyncio.Event()

        async def close() -> None:
            async with work.releasing_on_exit():
                closing.set()
            closed.set()

        invocation = asyncio.create_task(
            work.execute(ActivityLookupInput(key=Operation.A, private_note=work.note))
        )
        cleanup: asyncio.Task[None] | None = None
        try:
            await peer.entered.wait()
            cleanup = asyncio.create_task(close())
            await closing.wait()
            assert not closed.is_set()
            peer.release.set()
            assert await invocation == work[Operation.A].result
            await cleanup
            assert closed.is_set()
        finally:
            peer.release.set()
            work[Operation.A].release.set()
            await invocation
            if cleanup is not None:
                await cleanup
