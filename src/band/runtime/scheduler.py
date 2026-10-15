"""Serialized discovery, selection and dispatch for one room."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING

from band.core.exceptions import RoomExecutionStoppedError
from band.core.types import metadata_to_dict
from band.platform.event import MessageEvent, PlatformEvent
from band.platform.message_lifecycle import ActionablePage
from band.runtime.delivery import TurnOutcome
from band.runtime.messages import PlatformMessage
from band.runtime.originals import (
    backlog_event,
    delivery_id,
    is_ordinary,
    is_synthetic,
    message_id,
)
from band.runtime.resync import ResyncSchedule
from band.runtime.selection import (
    Candidate,
    Disposition,
    MessageKey,
    Stop,
    merge,
    read_reaches,
    select_prefix,
)

if TYPE_CHECKING:
    from band.runtime.execution import ExecutionContext

logger = logging.getLogger(__name__)


class ResyncRequest:
    """A coalesced request served at the next serial boundary."""

    type = "_resync"


class ResyncOutcome(Enum):
    EMPTY = "empty"
    WORK = "work"
    BLOCKED = "blocked"
    RETRY = "retry"
    STOPPED = "stopped"
    PROGRESS = "progress"


@dataclass(eq=False)
class DiscoveryPass:
    """Decisions that expire when one scheduler wake finishes."""

    recovery: bool
    declined: set[tuple[int, str]] = field(default_factory=set)
    control_page: ActionablePage | None = None
    ordinary_blocked: bool = False
    invalidated: bool = False
    control_boundaries: set[tuple[int, str]] = field(default_factory=set)
    resumed_retries: set[str] = field(default_factory=set)
    gate_message: PlatformMessage | None = None
    selections: set[tuple[bool, int, tuple[str, ...], Disposition]] = field(
        default_factory=set
    )


@dataclass(frozen=True)
class DispatchResult:
    scheduling: ResyncOutcome
    turn: TurnOutcome | None


class Scheduler:
    """One consumer and retained event line for all default-runtime input."""

    def __init__(self, owner: ExecutionContext) -> None:
        self.owner = owner
        self.queue = owner.queue
        self.pending: deque[PlatformEvent] = deque()
        self.sync_complete = owner.config.consume_all_queued
        self.first_ws_id: str | None = None
        self.deferred_id: str | None = None
        self.scan_cursor: str | None = None
        self.control_cursor: str | None = None
        self.pending_resync: ResyncRequest | None = None
        self.schedule = ResyncSchedule(
            owner.config.idle_resync_seconds,
            owner.config.idle_resync_max_seconds,
            stopped=owner.config.stopped_resync_seconds,
        )
        self.queue_wait: asyncio.Timeout | None = None
        self.handled_work = False
        self.current_pass: DiscoveryPass | None = None
        self.next_fallback = False

    @property
    def deferred(self) -> bool:
        return (
            self.deferred_id is not None
            or self.owner.claims.has_retryable_settlement(self.owner.room_id)
        )

    def defer(self, message: str | None) -> None:
        if message != self.deferred_id:
            self.deferred_id = message
            self.control_cursor = None

    def request_resync(self) -> None:
        self.owner.delivery.retry_not_before.clear()
        if self.pending_resync is None:
            self.pending_resync = ResyncRequest()
            self.queue.put_nowait(self.pending_resync)  # type: ignore[arg-type]

    def reset_interval(self) -> None:
        if self.schedule.due_at is None:
            return
        self.schedule.reset(
            asyncio.get_running_loop().time(), stopped=self.owner.is_stopped
        )
        if self.queue_wait is not None and not self.queue_wait.expired():
            self.queue_wait.reschedule(self.schedule.due_at)

    def unpark(self) -> None:
        self.owner.claims.unpark_acks(self.owner.room_id)

    def retain(self, event: PlatformEvent) -> None:
        identity = delivery_id(event)
        if identity is not None and any(
            delivery_id(e) == identity for e in self.pending
        ):
            return
        self.pending.append(event)

    def housekeeping(self) -> PlatformEvent | None:
        return next(
            (event for event in self.pending if not isinstance(event, MessageEvent)),
            None,
        )

    def release(self, identities: Iterable[str]) -> None:
        released = set(identities)
        self.pending = deque(
            event
            for event in self.pending
            if delivery_id(event) not in released
            or (
                is_ordinary(event)
                and self.owner.delivery.awaits_retry_confirmation(message_id(event))
            )
        )

    def receive(self, event: PlatformEvent) -> None:
        if (
            isinstance(event, MessageEvent)
            and self.first_ws_id is None
            and not self.sync_complete
        ):
            self.first_ws_id = delivery_id(event)
        self.retain(event)

    def absorb(self) -> None:
        live_request: ResyncRequest | None = None
        while True:
            try:
                event = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(event, ResyncRequest):
                if event is self.pending_resync:
                    live_request = event
            else:
                self.receive(event)
        # A request arriving inside a pass still owns a subsequent wake.
        if live_request is not None:
            self.queue.put_nowait(live_request)  # type: ignore[arg-type]

    @contextmanager
    def discovery(self, *, recovery: bool = False) -> Iterator[DiscoveryPass]:
        if self.current_pass is not None:
            yield self.current_pass
            return
        active = DiscoveryPass(recovery)
        self.current_pass = active
        try:
            yield active
        finally:
            self.current_pass = None

    def declined(self, identity: str) -> bool:
        active = self.current_pass
        return (
            active is not None
            and (self.owner.control_revision, identity) in active.declined
        )

    def record_decline(
        self, active: DiscoveryPass | None, revision: int, identity: str
    ) -> None:
        if (
            active is not None
            and self.current_pass is active
            and revision == self.owner.control_revision
        ):
            active.declined.add((revision, identity))

    async def run(self) -> None:
        try:
            self.pending_resync = None
            await self.run_pass(recovery=True)
            self.owner.finish_startup()
            self.schedule.start(asyncio.get_running_loop().time())
            while not self.owner.is_closing:
                deadline = self.schedule.due_at
                if (
                    deadline is not None
                    and deadline <= asyncio.get_running_loop().time()
                ):
                    self.pending_resync = None
                    await self.run_pass(recovery=True)
                    continue
                try:
                    # Shutdown cancellation belongs to the scheduler owner task.
                    async with asyncio.timeout_at(deadline) as self.queue_wait:
                        event = await self.queue.get()
                except TimeoutError:
                    continue
                finally:
                    self.queue_wait = None
                if isinstance(event, ResyncRequest):
                    if event is self.pending_resync:
                        self.pending_resync = None
                        await self.run_pass(recovery=True)
                else:
                    self.receive(event)
                    await self.run_pass()
        except asyncio.CancelledError:
            logger.debug("Room %s scheduler cancelled", self.owner.room_id)
        except Exception:
            logger.exception("Room %s scheduler failed", self.owner.room_id)

    async def retry_settlements(self) -> bool:
        results: list[bool] = []
        for identity in self.owner.claims.pending_ack_ids(self.owner.room_id):
            retry = self.owner.delivery.awaits_retry_confirmation(identity)
            results.append(await self.owner.delivery.retry_settlement(identity))
            if (
                retry
                and not self.owner.delivery.awaits_retry_confirmation(identity)
                and self.current_pass is not None
            ):
                self.current_pass.resumed_retries.add(identity)
        return all(results)

    async def run_pass(self, *, recovery: bool = False) -> ResyncOutcome:
        if recovery:
            self.owner.delivery.retry_not_before.clear()
        self.handled_work = False
        self.scan_cursor = None
        self.next_fallback = False
        with self.discovery(recovery=recovery) as active:
            self.absorb()
            outcome = ResyncOutcome.RETRY
            try:
                if self.owner.is_stopped:
                    gate_outcome = (
                        await self.resume_from_gate(active)
                        if recovery
                        else ResyncOutcome.STOPPED
                    )
                    if gate_outcome is ResyncOutcome.BLOCKED:
                        return gate_outcome
                active.ordinary_blocked = (
                    self.owner.is_stopped
                    or not await self.retry_settlements()
                    or (self.deferred and not recovery)
                )
                outcome = ResyncOutcome.PROGRESS
                while outcome is ResyncOutcome.PROGRESS and not self.owner.is_closing:
                    if not recovery and (
                        self.pending_resync is not None
                        or (
                            self.schedule.due_at is not None
                            and self.schedule.due_at
                            <= asyncio.get_running_loop().time()
                        )
                    ):
                        return ResyncOutcome.BLOCKED
                    housekeeping = (
                        self.housekeeping()
                        if active.ordinary_blocked or self.owner.is_stopped
                        else None
                    )
                    if housekeeping is not None:
                        outcome = (await self.dispatch(housekeeping)).scheduling
                    elif self.owner.is_stopped:
                        outcome = ResyncOutcome.STOPPED
                    elif self.deferred and active.ordinary_blocked:
                        outcome = await self.priority_step(active)
                    else:
                        outcome = await self.step(active)
                        if (
                            outcome in {ResyncOutcome.BLOCKED, ResyncOutcome.RETRY}
                            and self.deferred
                        ):
                            active.ordinary_blocked = True
                            outcome = ResyncOutcome.PROGRESS
                    if active.invalidated:
                        outcome = ResyncOutcome.BLOCKED
                    elif (
                        outcome is ResyncOutcome.STOPPED
                        and self.housekeeping() is not None
                    ):
                        outcome = ResyncOutcome.PROGRESS
                    if outcome is ResyncOutcome.PROGRESS:
                        await asyncio.sleep(0)
                if outcome is ResyncOutcome.EMPTY:
                    self.defer(None)
                    return ResyncOutcome.WORK if self.handled_work else outcome
                return outcome
            except RoomExecutionStoppedError:
                return ResyncOutcome.STOPPED
            except Exception:
                logger.exception("Room %s discovery failed", self.owner.room_id)
                return ResyncOutcome.RETRY
            finally:
                if recovery:
                    self.schedule.complete(
                        asyncio.get_running_loop().time(),
                        empty=outcome is ResyncOutcome.EMPTY and not self.handled_work,
                        stopped=self.owner.is_stopped,
                    )

    async def resume_from_gate(self, active: DiscoveryPass) -> ResyncOutcome | None:
        revision = self.owner.control_revision
        candidate = await self.owner.link.get_next_message(self.owner.room_id)
        self.absorb()
        if revision != self.owner.control_revision:
            return ResyncOutcome.BLOCKED
        if candidate is None:
            return ResyncOutcome.STOPPED
        if self.owner.delivery.eligibility(candidate) not in {
            "runnable",
            "pending",
            "inflight",
        }:
            return ResyncOutcome.STOPPED
        self.owner.resume_from_gate()
        active.gate_message = candidate
        return None

    def candidates(
        self, messages: Iterable[PlatformMessage], events: Iterable[MessageEvent]
    ) -> list[Candidate]:
        rest = [
            Candidate(
                MessageKey.of_message(row),
                backlog_event(row),
                Disposition.RUNNABLE,
                metadata_to_dict(row.metadata),
            )
            for row in messages
        ]
        retained = [
            Candidate(MessageKey.of_event(event), event, Disposition.RUNNABLE)
            for event in events
        ]
        return [self.classify(candidate) for candidate in merge(rest, retained)]

    def candidate(
        self, event: MessageEvent, *, observation: PlatformMessage | None = None
    ) -> Candidate:
        candidate = Candidate(
            MessageKey.of_event(event),
            event,
            Disposition.RUNNABLE,
            metadata_to_dict(observation.metadata) if observation is not None else None,
        )
        return self.classify(candidate)

    def classify(self, candidate: Candidate) -> Candidate:
        match self.owner.delivery.eligibility(candidate, remember_completion=False):
            case "runnable":
                active = self.current_pass
                boundary = (self.owner.control_revision, candidate.message_id)
                if self.owner.recognizes_delivery_control(candidate.event):
                    if active is not None:
                        active.control_boundaries.add(boundary)
                    disposition = Disposition.CONTROL
                elif active is not None and boundary in active.control_boundaries:
                    disposition = Disposition.BOUNDARY
                else:
                    disposition = Disposition.RUNNABLE
            case "inflight":
                disposition = Disposition.HELD
            case _:
                disposition = Disposition.SKIP
        return replace(candidate, disposition=disposition)

    def local_segment(self) -> tuple[list[MessageEvent], MessageKey | None, set[str]]:
        segment: list[MessageEvent] = []
        later: set[str] = set()
        bounded = False
        for event in self.pending:
            if not is_ordinary(event):
                bounded = True
            elif bounded:
                later.add(message_id(event))
            else:
                segment.append(event)
        limit = MessageKey.of_event(segment[-1]) if bounded and segment else None
        return segment, limit, later

    def prune_skipped(
        self,
        candidates: Iterable[Candidate],
        frontier: MessageKey | None,
        *,
        exhausted: bool,
    ) -> set[str]:
        skipped: set[str] = set()
        for candidate in candidates:
            covered = candidate.observed_metadata is not None or read_reaches(
                candidate, frontier, exhausted
            )
            if candidate.disposition is not Disposition.SKIP or not covered:
                continue
            if self.owner.delivery.eligibility(candidate) != "runnable":
                skipped.add(candidate.message_id)
        removable = [
            message_id(event)
            for event in self.pending
            if is_ordinary(event)
            and message_id(event) in skipped
            and not self.owner.delivery.awaits_retry_confirmation(message_id(event))
        ]
        if removable:
            logger.debug(
                "Room %s: Releasing retained message(s) %s that no turn will run",
                self.owner.room_id,
                removable,
            )
        self.release(skipped)
        return skipped

    async def step(self, active: DiscoveryPass) -> ResyncOutcome:
        self.absorb()
        if self.owner.is_stopped or self.owner.is_closing:
            return ResyncOutcome.STOPPED
        recovery = active.recovery or not self.sync_complete
        if (
            (not recovery or self.owner.config.consume_all_queued)
            and self.pending
            and not is_ordinary(self.pending[0])
        ):
            return (await self.dispatch(self.pending[0])).scheduling
        revision = self.owner.control_revision
        if not self.owner.config.consume_all_queued and not recovery:
            rows: list[PlatformMessage] = []
            exhausted = True
            next_cursor = None
        elif self.owner.config.consume_all_queued or self.next_fallback:
            page = await self.owner.link.get_actionable_page(
                self.owner.room_id, cursor=self.scan_cursor
            )
            self.absorb()
            if revision != self.owner.control_revision:
                return ResyncOutcome.PROGRESS
            rows = page.messages
            exhausted = page.next_cursor is None
            next_cursor = page.next_cursor
        else:
            row = active.gate_message
            active.gate_message = None
            if row is None:
                row = await self.owner.link.get_next_message(self.owner.room_id)
            self.absorb()
            if revision != self.owner.control_revision:
                return ResyncOutcome.PROGRESS
            rows = [row] if row is not None else []
            exhausted = row is None
            next_cursor = None
            if row is None:
                self.sync_complete = True
                active.recovery = False
            elif (
                self.candidate(backlog_event(row), observation=row).disposition
                is Disposition.SKIP
            ):
                self.next_fallback = True
                return ResyncOutcome.PROGRESS
        if self.owner.is_stopped:
            return ResyncOutcome.STOPPED
        if (
            not rows
            and self.sync_complete
            and self.pending
            and not is_ordinary(self.pending[0])
        ):
            return (await self.dispatch(self.pending[0])).scheduling
        segment, limit, later = self.local_segment()
        batching = self.owner.config.consume_all_queued
        candidates = self.candidates(
            (row for row in rows if not batching or row.id not in later),
            segment
            if batching
            else (event for event in self.pending if is_ordinary(event)),
        )
        rest_ids = {row.id for row in rows}
        leading_ids = {message_id(event) for event in segment}
        if batching:
            candidates.sort(key=lambda candidate: candidate.key)
        else:
            candidates.sort(
                key=lambda candidate: candidate.message_id not in active.resumed_retries
            )
        frontier = MessageKey.of_message(rows[-1]) if rows else None
        selection = select_prefix(
            candidates,
            listed=rest_ids,
            retained=leading_ids,
            cap=self.owner.config.max_queued_messages
            if self.owner.config.consume_all_queued
            else 1,
            page_reaches=frontier,
            exhausted=exhausted,
            limit=limit if batching else None,
            resumed=active.resumed_retries,
        )
        skipped = self.prune_skipped(candidates, frontier, exhausted=exhausted)
        if selection.members:
            result = (await self.dispatch(selection.members)).scheduling
            if (
                result is ResyncOutcome.PROGRESS
                and not self.owner.config.consume_all_queued
            ):
                self.next_fallback = False
                self.scan_cursor = None
                if any(c.message_id == self.first_ws_id for c in selection.members):
                    self.sync_complete = True
                    self.first_ws_id = None
                    if self.schedule.due_at is None:
                        active.recovery = False
            return result
        if selection.stop is Stop.HELD and selection.at is not None:
            logger.debug(
                "Room %s: Message %s is held by another claim; deferring behind it",
                self.owner.room_id,
                selection.at.message_id,
            )
            self.defer(selection.at.message_id)
            return ResyncOutcome.BLOCKED
        if next_cursor is not None and selection.stop is not Stop.LIMIT:
            self.scan_cursor = next_cursor
            return ResyncOutcome.PROGRESS
        self.scan_cursor = None
        if exhausted:
            self.sync_complete = True
            active.recovery = False
        if skipped and any(
            delivery_id(event) in skipped
            and not self.owner.delivery.awaits_retry_confirmation(message_id(event))
            for event in segment
        ):
            return ResyncOutcome.PROGRESS
        if self.pending and self.sync_complete and not is_ordinary(self.pending[0]):
            return ResyncOutcome.PROGRESS
        return ResyncOutcome.EMPTY

    async def dispatch(
        self, selected: tuple[Candidate, ...] | PlatformEvent, *, priority: bool = False
    ) -> DispatchResult:
        if not isinstance(selected, tuple):
            return await self.dispatch_local(selected)
        members = selected
        active = self.current_pass
        identities = tuple(candidate.message_id for candidate in members)
        revision = self.owner.control_revision
        if self.owner.is_stopped:
            return DispatchResult(ResyncOutcome.STOPPED, TurnOutcome.PAUSED)
        control = members[0].disposition is Disposition.CONTROL and not self.declined(
            identities[0]
        )
        if priority and not control:
            return DispatchResult(ResyncOutcome.BLOCKED, TurnOutcome.DECLINED)
        if active is not None:
            key = (priority, revision, identities, members[0].disposition)
            if key in active.selections:
                logger.debug(
                    "Room %s: Selection %s repeated without progress; leaving it for the next resync",
                    self.owner.room_id,
                    identities,
                )
                return DispatchResult(ResyncOutcome.BLOCKED, None)
            active.selections.add(key)
        if control:
            outcome = await self.owner.delivery.run(members, revision, control=True)
            if outcome is TurnOutcome.DECLINED:
                self.record_decline(active, revision, identities[0])
                if priority:
                    return DispatchResult(ResyncOutcome.BLOCKED, outcome)
            else:
                return self.after_delivery(
                    outcome, identities, revision, priority=priority, control=True
                )
        outcome = await self.owner.delivery.run(members, revision)
        return self.after_delivery(outcome, identities, revision, priority=priority)

    def after_delivery(
        self,
        outcome: TurnOutcome,
        identities: tuple[str, ...],
        revision: int,
        *,
        priority: bool,
        control: bool = False,
    ) -> DispatchResult:
        match outcome:
            case TurnOutcome.ADVANCED:
                self.release(identities)
                if self.deferred_id in identities:
                    self.defer(None)
                scheduling = ResyncOutcome.PROGRESS
            case _ if self.owner.is_stopped or self.owner.is_closing:
                scheduling = ResyncOutcome.STOPPED
            case _ if revision != self.owner.control_revision:
                if self.current_pass is not None:
                    self.current_pass.invalidated = True
                if not control and not priority and identities:
                    self.defer(identities[0])
                scheduling = ResyncOutcome.BLOCKED
            case TurnOutcome.NOT_ACCEPTED | TurnOutcome.RETRY_LATER:
                if not priority and identities:
                    self.defer(identities[0])
                scheduling = (
                    ResyncOutcome.RETRY
                    if outcome is TurnOutcome.RETRY_LATER
                    else ResyncOutcome.BLOCKED
                )
            case _:
                scheduling = ResyncOutcome.BLOCKED
        return DispatchResult(scheduling, outcome)

    async def dispatch_local(self, event: PlatformEvent) -> DispatchResult:
        if self.owner.is_stopped and isinstance(event, MessageEvent):
            return DispatchResult(ResyncOutcome.STOPPED, TurnOutcome.PAUSED)
        revision = self.owner.control_revision
        outcome = await self.owner.execute_local(event)
        if outcome is TurnOutcome.ADVANCED or not is_synthetic(event):
            self.pending = deque(item for item in self.pending if item is not event)
        identities = (message_id(event),) if is_synthetic(event) else ()
        return self.after_delivery(outcome, identities, revision, priority=False)

    async def priority_step(self, active: DiscoveryPass) -> ResyncOutcome:
        if self.owner._on_message_control is None:
            return ResyncOutcome.BLOCKED
        revision = self.owner.control_revision
        if active.control_page is None:
            page = await self.owner.link.get_actionable_page(
                self.owner.room_id, cursor=self.control_cursor
            )
            self.absorb()
            if self.owner.is_stopped or revision != self.owner.control_revision:
                return (
                    ResyncOutcome.STOPPED
                    if self.owner.is_stopped
                    else ResyncOutcome.PROGRESS
                )
            active.control_page = page
            self.control_cursor = page.next_cursor
        candidates = self.candidates(
            active.control_page.messages,
            (event for event in self.pending if is_ordinary(event)),
        )
        frontier = (
            MessageKey.of_message(active.control_page.messages[-1])
            if active.control_page.messages
            else None
        )
        for candidate in candidates:
            if candidate.disposition is Disposition.SKIP:
                skipped = self.prune_skipped(
                    (candidate,),
                    frontier,
                    exhausted=active.control_page.next_cursor is None,
                )
                if (
                    candidate.message_id in skipped
                    and candidate.message_id == self.deferred_id
                    and not self.owner.delivery.awaits_retry_confirmation(
                        candidate.message_id
                    )
                ):
                    self.defer(None)
            elif candidate.disposition is Disposition.CONTROL and not self.declined(
                candidate.message_id
            ):
                key = (True, revision, (candidate.message_id,), candidate.disposition)
                if key not in active.selections:
                    result = await self.dispatch((candidate,), priority=True)
                    if result.turn is TurnOutcome.ADVANCED:
                        active.ordinary_blocked = (
                            self.owner.claims.has_retryable_settlement(
                                self.owner.room_id
                            )
                        )
                        active.selections = {key for key in active.selections if key[0]}
                    return (
                        ResyncOutcome.STOPPED
                        if result.scheduling is ResyncOutcome.STOPPED
                        else ResyncOutcome.PROGRESS
                    )
        return ResyncOutcome.BLOCKED if self.deferred else ResyncOutcome.PROGRESS
