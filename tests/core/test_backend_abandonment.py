"""Abandonment ends admission waiting while settlement remains independently owned."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import ProviderUnavailableError
from band.core.turn_lifecycle import BackendTurns
from band.testing import FakeAgentTools
from tests.core.recordingcancel import RecordingCancel


@pytest.mark.looptime
class TestBackendAbandonment:
    async def test_ack_without_settlement_eventually_fails_input_then_recovers(
        self,
    ) -> None:
        cancel = RecordingCancel()
        turns = BackendTurns(cancel, provider="test", requires_confirmation=True)
        async with turns.running("room", "old"):
            await turns.stop("room")
        ready = turns.blocker("room")
        assert ready is not None
        with pytest.raises(ProviderUnavailableError):
            await ready
        with pytest.raises(ProviderUnavailableError):
            await turns.require_admission("room", FakeAgentTools())
        async with turns.running("other", "independent"):
            assert turns.active_handle("other") == "independent"
        turns.confirm_stopped("room", "wrong")
        assert turns.unavailable("room")
        turns.confirm_stopped("room", "old")
        async with turns.running("room", "new"):
            assert turns.active_handle("room") == "new"

    @pytest.mark.parametrize("requires_confirmation", [False, True])
    async def test_resistant_cancel_cannot_extend_the_caller_budget(
        self, requires_confirmation: bool
    ) -> None:
        entered, cancelled, release, finished = (asyncio.Event() for _ in range(4))

        async def cancel(handle: str) -> None:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            turns.confirm_stopped("room", handle)
            finished.set()

        turns = BackendTurns(
            cancel, provider="test", requires_confirmation=requires_confirmation
        )
        try:
            async with turns.running("room", "old"):
                turns.cancel("room")
                await entered.wait()
            await turns.settled("room")
            await cancelled.wait()
            with pytest.raises(ProviderUnavailableError):
                await turns.require_admission("room", FakeAgentTools())
            assert not finished.is_set()
        finally:
            release.set()
            await finished.wait()
        async with turns.running("room", "new"):
            assert turns.active_handle("room") == "new"

    async def test_unknown_start_stays_owned_after_abandonment(self) -> None:
        entered, release, cleaned = (asyncio.Event() for _ in range(3))

        async def start() -> str:
            entered.set()
            await release.wait()
            return "late"

        async def cancel(handle: str) -> None:
            assert handle == "late"
            turns.confirm_stopped("room", handle)
            cleaned.set()

        turns = BackendTurns(cancel, provider="test", requires_confirmation=True)

        async def run() -> None:
            async with turns.running("room"):
                await turns.started("room", start())

        caller = asyncio.create_task(run())
        try:
            await entered.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            await turns.settled("room")
            with pytest.raises(ProviderUnavailableError):
                await turns.require_admission("room", FakeAgentTools())
            assert not cleaned.is_set()
        finally:
            release.set()
            await cleaned.wait()
        await turns.require_admission("room", FakeAgentTools())
