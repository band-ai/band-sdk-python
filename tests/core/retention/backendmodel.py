"""Generate backend admission transitions against real retained-room release owners."""

from __future__ import annotations

import asyncio
from enum import StrEnum, auto

import pytest
from band_sdk_core import backend_stop_unconfirmed_message

from band.core.exceptions import InputNotAccepted
from band.core.retention import Residency, RetainedRoom
from band.core.turn_lifecycle import BackendTurns
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
        self.recovering = False
        self.reported = False
        self.report_fails = False
        self.reports = 0
        self.stale: list[tuple[str, RetainedRoom, int]] = []

    async def take(self, step: Step) -> None:
        match step:
            case Step.STOP:
                await self.stop()
            case Step.RECOVER if self.blocked:
                assert self.handle is not None
                self.resident.fail_close = True
                self.turns.recovering(
                    "room",
                    self.handle,
                    self.resident.room,
                    generation=self.resident.room.generation,
                )
                self.recovering = True
            case Step.RELEASE:
                await self.release()
            case Step.DISPOSE:
                await self.dispose()
            case Step.STALE:
                await self.confirm_stale()
            case Step.REPORT_FAILS:
                self.report_fails = True
                self.tools.send_event_error = RuntimeError("report unavailable")
            case Step.REPORT_RECOVERS:
                self.report_fails = False
                self.tools.send_event_error = None
            case Step.RECHECK | Step.RECOVER:
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
        self.recovering = self.reported = False

    async def release(self) -> None:
        if not self.blocked:
            return
        assert self.handle is not None
        if self.recovering:
            self.resident.fail_close = False
            await self.resident.room.wait_released(
                generation=self.resident.room.generation
            )
            assert not self.resident.live
            assert self.ready is not None and self.ready.done()
        else:
            self.turns.confirm_stopped("room", self.handle)
        self.blocked = False

    async def dispose(self) -> None:
        if self.blocked:
            assert self.handle is not None
            self.turns.recovering(
                "room",
                self.handle,
                self.resident.room,
                generation=self.resident.room.generation,
            )
        self.resident.fail_close = False
        await self.resident.room.dispose()
        self.turns.confirm_disposed("room")
        assert not self.resident.live
        self.blocked = False

    async def confirm_stale(self) -> None:
        for handle, room, generation in self.stale:
            self.turns.confirm_stopped("room", handle)
            if room is not self.resident.room or generation != room.generation:
                assert not room.recover(
                    generation=generation, reason="stale completion"
                )
        self.turns.confirm_disposed("room")

    async def check_admission(self) -> None:
        if self.blocked:
            with pytest.raises(InputNotAccepted) as refusal:
                await self.turns.require_admission("room", self.tools)
            assert refusal.value.ready is self.ready
            assert self.ready is not None and not self.ready.done()
            if self.recovering:
                assert self.resident.live
            if not self.reported and not self.report_fails:
                self.reports += 1
                self.reported = True
        else:
            await self.turns.require_admission("room", self.tools)
            assert self.ready is None or self.ready.done()
        assert (
            failure_reports(self.tools)
            == [("test", backend_stop_unconfirmed_message())] * self.reports
        )


async def play(steps: list[Step]) -> None:
    async with ResidenceHost() as host:
        model = BackendModel(host)
        try:
            for step in steps:
                await model.take(step)
            await model.take(Step.RELEASE)
        finally:
            await model.dispose()
            await model.turns.drain()
