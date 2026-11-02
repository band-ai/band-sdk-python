"""Real activity reports, ordered control execution and raw room pushes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Collection, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import Any, TypeVar

from band_rest import AgentActivityDetail
from phoenix_channels_python_client.phx_messages import ChannelMessage

from band.client.streaming import AgentControlPayload, ControlMode
from band.client.streaming.client import ControlHook
from band.platform import message_lifecycle
from band.platform.link import BandLink
from band.runtime.activity import TOOL_CALL
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient


class ActivityPush(StrEnum):
    STARTED = "agent_activity_started"
    CHANGED = "agent_activity_changed"
    STOPPED = "agent_activity_stopped"


@dataclass(frozen=True)
class ReportOutcome:
    accepted: bool
    stopped: bool


@dataclass(frozen=True)
class ActivityReport:
    sequence: int
    room_id: str
    agent_id: str
    working: bool
    activity: dict[str, Any] | None
    outcome: asyncio.Future[ReportOutcome]


@dataclass(frozen=True)
class ControlExecution:
    sequence: int
    payload: AgentControlPayload
    completed: asyncio.Future[None]


@dataclass(frozen=True)
class RawActivity:
    sequence: int
    room_id: str
    event: str
    payload: dict[str, Any]


T = TypeVar("T")

DETAIL_DISABLED = "activity detail disabled in this process"
UNLISTED_TOOL = "activity named a tool outside the allowed names"
EXTRA_DETAIL = "activity detail carries more than a kind and tool name"


class StopObservation:
    def __init__(self, callback: Callable[[], None] | None) -> None:
        self._callback = callback
        self.seen = False

    def receive(self) -> None:
        self.seen = True
        if self._callback is not None:
            self._callback()


class ActivityCapture:
    """Sequence cursors distinguish dispatch from a delayed response."""

    def __init__(self, *, deadline_s: float = 300) -> None:
        self.deadline_s = deadline_s
        self.reports: list[ActivityReport] = []
        self.controls: list[ControlExecution] = []
        self.raw: list[RawActivity] = []
        self._sequence = 0
        self._changed = asyncio.Event()

    def cursor(self) -> int:
        return self._sequence

    def _recorded(self) -> int:
        self._sequence += 1
        self._changed.set()
        return self._sequence

    def link(self, **kwargs: Any) -> ObservingBandLink:
        return ObservingBandLink(capture=self, **kwargs)

    def dispatched(
        self,
        room_id: str,
        agent_id: str,
        working: bool,
        activity: AgentActivityDetail | None,
    ) -> ActivityReport:
        report = ActivityReport(
            self._recorded(),
            room_id,
            agent_id,
            working,
            activity.model_dump(exclude_none=True) if activity is not None else None,
            asyncio.get_running_loop().create_future(),
        )
        self.reports.append(report)
        return report

    def control_entered(self, payload: AgentControlPayload) -> ControlExecution:
        control = ControlExecution(
            self._recorded(), payload, asyncio.get_running_loop().create_future()
        )
        self.controls.append(control)
        return control

    async def _wait(
        self, records: Sequence[T], matches: Callable[[T], bool], phase: str
    ) -> T:
        try:
            async with asyncio.timeout(self.deadline_s):
                while True:
                    self._changed.clear()
                    if (
                        selected := next((r for r in records if matches(r)), None)
                    ) is not None:
                        return selected
                    await self._changed.wait()
        except TimeoutError:
            raise TimeoutError(
                f"Missing activity barrier: {phase}; observed={self.outline()}"
            ) from None

    def outline(self) -> dict[str, object]:
        """Describe transport ordering without tool arguments or result content."""
        return {
            "reports": [
                (report.sequence, report.room_id, report.working, report.activity)
                for report in self.reports[-8:]
            ],
            "raw": [
                (
                    event.sequence,
                    event.room_id,
                    event.event,
                    event.payload.get("agent_id"),
                )
                for event in self.raw[-32:]
            ],
        }

    async def report(
        self,
        room_id: str,
        *,
        agent_id: str | None = None,
        working: bool,
        activity: dict[str, Any] | None = None,
        since: int = 0,
    ) -> ActivityReport:
        record = await self._wait(
            self.reports,
            lambda r: (
                r.sequence > since
                and r.room_id == room_id
                and (agent_id is None or r.agent_id == agent_id)
                and r.working is working
                and (activity is None or r.activity == activity)
            ),
            f"report dispatched room={room_id} working={working}",
        )
        async with asyncio.timeout(self.deadline_s):
            outcome = await asyncio.shield(record.outcome)
        assert outcome == ReportOutcome(accepted=True, stopped=False), (record, outcome)
        # The record holds the detail the SDK meant to send; a latched rejection
        # strips it from the wire while the report is still accepted.
        assert activity is None or message_lifecycle.activity_support.supported, (
            DETAIL_DISABLED,
            record,
        )
        return record

    async def held(
        self,
        room_id: str,
        agent_id: str,
        activity: dict[str, Any],
        *,
        since: int,
        entered: int,
    ) -> ActivityReport:
        """Await detail pushed after ``since`` and re-reported after ``entered``."""
        await self.detail(room_id, agent_id, activity, since=since)
        return await self.report(
            room_id, agent_id=agent_id, working=True, activity=activity, since=entered
        )

    async def finished(self, room_id: str, agent_id: str, *, since: int) -> RawActivity:
        """Await an accepted idle report and the platform's stop after ``since``."""
        await self.report(room_id, agent_id=agent_id, working=False, since=since)
        return await self.stopped(room_id, agent_id, since=since)

    async def control(
        self, room_id: str, *, since: int, agent_id: str | None = None
    ) -> ControlExecution:
        return await self._wait(
            self.controls,
            lambda c: (
                c.sequence > since
                and c.payload.room_id == room_id
                and (agent_id is None or c.payload.agent_id == agent_id)
                and c.payload.mode == ControlMode.STOP
            ),
            f"STOP handler entry room={room_id}",
        )

    async def control_finished(self, control: ControlExecution) -> None:
        async with asyncio.timeout(self.deadline_s):
            await asyncio.shield(control.completed)

    async def _receive(self, room_id: str, message: ChannelMessage) -> None:
        self.raw.append(
            RawActivity(
                self._recorded(), room_id, str(message.event), dict(message.payload)
            )
        )

    @asynccontextmanager
    async def rooms(
        self, ws: TrackingWebSocketClient, *room_ids: str
    ) -> AsyncIterator[None]:
        client = ws.ws.client
        if client is None:
            raise RuntimeError("Activity capture requires a connected user observer")
        async with AsyncExitStack() as subscriptions:
            for room_id in room_ids:
                topic = f"room_activity:{room_id}"
                await client.subscribe_to_topic(topic, partial(self._receive, room_id))
                subscriptions.push_async_callback(client.unsubscribe_from_topic, topic)
            yield

    async def detail(
        self, room_id: str, agent_id: str, activity: dict[str, Any], *, since: int = 0
    ) -> RawActivity:
        return await self._wait(
            self.raw,
            lambda e: (
                e.sequence > since
                and e.room_id == room_id
                and e.payload.get("agent_id") == agent_id
                and e.event in (ActivityPush.STARTED, ActivityPush.CHANGED)
                and e.payload.get("activity") == activity
            ),
            f"raw detail room={room_id} activity={activity}",
        )

    async def stopped(self, room_id: str, agent_id: str, *, since: int) -> RawActivity:
        return await self._wait(
            self.raw,
            lambda e: (
                e.sequence > since
                and e.room_id == room_id
                and e.payload.get("agent_id") == agent_id
                and e.event == ActivityPush.STOPPED
            ),
            f"raw stop room={room_id}",
        )

    def assert_private(
        self, *sentinels: str, tool_names: Collection[str] | None = None
    ) -> None:
        """Assert no sentinel reached a push and details carry only a kind and
        a tool name, one of ``tool_names`` when given."""
        payload = json.dumps([event.payload for event in self.raw])
        for sentinel in sentinels:
            assert sentinel and sentinel not in payload
        details = [
            activity
            for event in self.raw
            if (activity := event.payload.get("activity")) is not None
        ]
        for activity in details:
            expected = {"kind": activity["kind"]}
            if activity["kind"] == TOOL_CALL:
                expected["name"] = activity["name"]
            assert activity == expected, (EXTRA_DETAIL, activity)
        if tool_names is not None:
            reported = {a["name"] for a in details if a["kind"] == TOOL_CALL}
            assert reported <= set(tool_names), (UNLISTED_TOOL, reported)


class ObservingBandLink(BandLink):
    """Observe construction-injected real I/O without replacing its decisions."""

    def __init__(self, *, capture: ActivityCapture, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.capture = capture

    async def report_activity(
        self,
        room_id: str,
        working: bool,
        *,
        activity: AgentActivityDetail | None = None,
        timeout_seconds: int = 2,
        on_stopped: Callable[[], None] | None = None,
    ) -> bool:
        record = self.capture.dispatched(room_id, self.agent_id, working, activity)
        stop = StopObservation(on_stopped)
        try:
            accepted = await super().report_activity(
                room_id,
                working,
                activity=activity,
                timeout_seconds=timeout_seconds,
                on_stopped=stop.receive,
            )
        except BaseException as error:
            record.outcome.set_exception(error)
            record.outcome.exception()
            raise
        record.outcome.set_result(ReportOutcome(accepted, stop.seen))
        return accepted

    async def _observe_control(
        self, hook: ControlHook, payload: AgentControlPayload
    ) -> None:
        record = self.capture.control_entered(payload)
        try:
            await hook(payload)
        except BaseException as error:
            record.completed.set_exception(error)
            record.completed.exception()
            raise
        record.completed.set_result(None)

    async def _apply_control(
        self,
        previous: asyncio.Task[None] | None,
        hook: ControlHook,
        payload: AgentControlPayload,
    ) -> None:
        await super()._apply_control(
            previous, partial(self._observe_control, hook), payload
        )
