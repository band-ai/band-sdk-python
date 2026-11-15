"""Generate backend admission transitions against real retained-room release owners."""

from __future__ import annotations

import asyncio
from enum import StrEnum, auto

import pytest
from band_sdk_core import backend_stop_unconfirmed_message

from band.core.exceptions import InputNotAccepted, ProviderUnavailableError
from band.core.retention import Residency, RetainedRoom
from band.core.turn_lifecycle import PROVIDER_UNAVAILABLE_MESSAGE, BackendTurns
from band.testing import FakeAgentTools, failure_reports
from tests.core.recordingcancel import RecordingCancel
from tests.core.retention.kit import ResidenceHost


class Step(StrEnum):
    STOP = auto()
    RECHECK = auto()
    RECOVER = auto()
    RELEASE = auto()
    DISPOSE = auto()
    STALE = auto()
    REPORT_FAILS = auto()
    REPORT_RECOVERS = auto()
    UNAVAILABLE = auto()
    LATE_CONFIRM = auto()
    REOPEN = auto()


class BackendModel:
    def __init__(self, host: ResidenceHost) -> None:
        self.host = host
        self.resident = host.room()
        self.turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        self.tools = FakeAgentTools()
        self.handle: str | None = None
        self.ready: asyncio.Future[None] | None = None
        self.blocked = False
        self.unavailable = False
        self.recovering = False
        self.blocked_reported = False
        self.unavailable_reported = False
        self.report_fails = False
        self.posted: list[tuple[str, str]] = []
        self.stale: list[tuple[str, RetainedRoom, int]] = []

    async def take(self, step: Step) -> None:
        match step:
            case Step.STOP:
                await self.stop()
            case Step.RECOVER if self.blocked and not self.unavailable:
                assert self.handle is not None
                self.resident.fail_close = True
                self.turns.recovering(
                    "room",
                    self.handle,
                    self.resident.room,
                    generation=self.resident.room.generation,
                )
                self.recovering = True
            case Step.UNAVAILABLE if self.blocked and not self.unavailable:
                self.turns.mark_unavailable("room")
                self.unavailable = True
                self.ready = self.turns.blocker("room")
            case Step.RELEASE:
                await self.release()
            case Step.DISPOSE:
                await self.dispose()
            case Step.STALE | Step.LATE_CONFIRM:
                await self.confirm_stale()
            case Step.REOPEN if (
                self.unavailable and self.handle is not None and not self.recovering
            ):
                # Evidence for a stop with no owned recovery; a registered
                # recovery reopens only through its release (RELEASE step).
                self.turns.confirm_stopped("room", self.handle)
                self.blocked = False
                self.unavailable = False
                self.recovering = False
            case Step.REPORT_FAILS:
                self.report_fails = True
                self.tools.send_event_error = RuntimeError("report unavailable")
            case Step.REPORT_RECOVERS:
                self.report_fails = False
                self.tools.send_event_error = None
            case Step.RECHECK | Step.RECOVER | Step.UNAVAILABLE | Step.REOPEN:
                pass
        await self.check_admission()

    async def stop(self) -> None:
        if self.blocked:
            self.turns.cancel("room")
            return
        if self.handle is not None:
            self.stale.append(
                (self.handle, self.resident.room, self.resident.room.generation)
            )
        if self.resident.room.snapshot.state is Residency.CLOSED:
            self.resident = self.host.room()
        await self.resident.turn()
        self.handle = f"operation-{len(self.stale)}"
        async with self.turns.running("room", self.handle):
            self.turns.cancel("room")
        await self.turns.drain()
        self.ready = self.turns.blocker("room")
        assert self.ready is not None
        self.blocked = True
        self.unavailable = False
        self.recovering = False
        self.blocked_reported = False
        self.unavailable_reported = False

    async def release(self) -> None:
        if not self.blocked:
            return
        assert self.handle is not None
        if self.unavailable:
            self.resident.fail_close = False
            if self.recovering and self.resident.live:
                # Only a registered recovery asked the room to retire; an
                # unavailable latch alone leaves the residency unrequested.
                await self.resident.room.wait_released(
                    generation=self.resident.room.generation
                )
            self.turns.confirm_stopped("room", self.handle)
            self.blocked = False
            self.unavailable = False
            self.recovering = False
            return
        if self.recovering:
            self.resident.fail_close = False
            try:
                await self.resident.room.wait_released(
                    generation=self.resident.room.generation
                )
            except Exception:  # noqa: BLE001 -- any failed release makes the room unavailable
                self.turns.mark_unavailable("room")
                self.unavailable = True
                self.ready = self.turns.blocker("room")
                return
            assert not self.resident.live
            assert self.ready is not None and self.ready.done()
            self.blocked = False
            self.recovering = False
        else:
            self.turns.confirm_stopped("room", self.handle)
            self.blocked = False

    async def dispose(self) -> None:
        if self.blocked and self.handle is not None and not self.unavailable:
            self.turns.recovering(
                "room",
                self.handle,
                self.resident.room,
                generation=self.resident.room.generation,
            )
        self.resident.fail_close = False
        await self.resident.room.dispose()
        self.turns.confirm_disposed("room")
        if (
            self.handle is not None
            and self.turns.unconfirmed_handle("room") is not None
        ):
            self.turns.confirm_stopped("room", self.handle)
        assert not self.resident.live
        self.blocked = False
        self.unavailable = False
        self.recovering = False

    async def confirm_stale(self) -> None:
        for handle, room, generation in self.stale:
            self.turns.confirm_stopped("room", handle)
            if room is not self.resident.room or generation != room.generation:
                assert not room.recover(
                    generation=generation, reason="stale completion"
                )
        self.turns.confirm_disposed("room")

    async def check_admission(self) -> None:
        if self.unavailable:
            with pytest.raises(ProviderUnavailableError):
                await self.turns.require_admission("room", self.tools)
            if not self.unavailable_reported and not self.report_fails:
                self.posted.append(("test", PROVIDER_UNAVAILABLE_MESSAGE))
                self.unavailable_reported = True
            assert self.turns.blocker("room") is None
        elif self.blocked:
            try:
                await self.turns.require_admission("room", self.tools)
            except ProviderUnavailableError:
                self.unavailable = True
                self.ready = self.turns.blocker("room")
                if not self.unavailable_reported and not self.report_fails:
                    self.posted.append(("test", PROVIDER_UNAVAILABLE_MESSAGE))
                    self.unavailable_reported = True
                return
            except InputNotAccepted as refusal:
                assert refusal.ready is self.ready
                assert self.ready is not None and not self.ready.done()
                if self.recovering:
                    assert self.resident.live
                if not self.blocked_reported and not self.report_fails:
                    self.posted.append(("test", backend_stop_unconfirmed_message()))
                    self.blocked_reported = True
                return
            raise AssertionError("blocked admission should have refused")
        else:
            await self.turns.require_admission("room", self.tools)
            assert self.ready is None or self.ready.done()
        assert failure_reports(self.tools) == self.posted


async def play(steps: list[Step]) -> None:
    async with ResidenceHost() as host:
        model = BackendModel(host)
        try:
            for step in steps:
                await model.take(step)
            if model.unavailable and model.handle is not None:
                model.turns.confirm_stopped("room", model.handle)
                model.blocked = False
                model.unavailable = False
            else:
                await model.take(Step.RELEASE)
        finally:
            await model.dispose()
            await model.turns.drain()
