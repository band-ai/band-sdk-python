"""Serialized discovery, selection and dispatch for one room."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING

from band.core.exceptions import RoomExecutionStoppedError
from band.platform.event import MessageEvent, PlatformEvent
from band.platform.message_lifecycle import ActionablePage
from band.runtime.delivery import TurnOutcome
from band.runtime.messages import PlatformMessage
from band.runtime.originals import (
    delivery_id,
    is_ordinary,
    is_synthetic,
    message_id,
)
from band.runtime.resync import ResyncSchedule
from band.runtime.retained import RetainedEvents
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

# Messages one turn consumes. Batching is deliberately unavailable, so every
# turn runs a single original.
SINGLETON_TURN_SIZE = 1


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


@dataclass(frozen=True)
class DispatchKey:
    """What a pass already dispatched; a repeat without progress is not run again."""

    priority: bool
    revision: int
    identities: tuple[str, ...]
    disposition: Disposition

    @classmethod
    def of(
        cls, members: tuple[Candidate, ...], revision: int, *, priority: bool
    ) -> DispatchKey:
        return cls(
            priority,
            revision,
            tuple(candidate.message_id for candidate in members),
            members[0].disposition,
        )


@dataclass(eq=False)
class DiscoveryPass:
    """Decisions that expire when one scheduler wake finishes."""

    recovery: bool
    declined: set[tuple[int, str]] = field(default_factory=set)
    control_page: ActionablePage | None = None
    # The deferred head already met in this pass; owed writes are read live.
    ordinary_held: bool = False
    invalidated: bool = False
    control_boundaries: set[tuple[int, str]] = field(default_factory=set)
    resumed_retries: set[str] = field(default_factory=set)
    gate_message: PlatformMessage | None = None
    selections: set[DispatchKey] = field(default_factory=set)


@dataclass(frozen=True)
class PageRead:
    """The platform rows one step selects from."""

    rows: list[PlatformMessage]
    exhausted: bool
    next_cursor: str | None = None


@dataclass(frozen=True)
class DispatchResult:
    scheduling: ResyncOutcome
    turn: TurnOutcome | None


class Scheduler:
    """One consumer and retained event line for all default-runtime input."""

    def __init__(self, owner: ExecutionContext) -> None:
        self.owner = owner
        self.queue = owner.queue
        self.pending = RetainedEvents()
        self.sync_complete = False
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
    def owes_writes(self) -> bool:
        return self.owner.claims.has_retryable_settlement(self.owner.room_id)

    @property
    def deferred(self) -> bool:
        return self.deferred_id is not None or self.owes_writes

    def ordinary_gated(self, active: DiscoveryPass) -> bool:
        """Whether ordinary input waits: an owed write, or a head this pass found held."""
        return self.owes_writes or (
            active.ordinary_held and self.deferred_id is not None
        )

    def defer(self, message: str | None) -> None:
        if message != self.deferred_id:
            self.deferred_id = message
            self.control_cursor = None

    def request_resync(self) -> None:
        self.owner.delivery.lift_write_backoff()
        if self.pending_resync is None:
            self.pending_resync = ResyncRequest()
            self.queue.put_nowait(self.pending_resync)

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

    def housekeeping(self) -> PlatformEvent | None:
        return next(
            (event for event in self.pending if not isinstance(event, MessageEvent)),
            None,
        )

    def holds_retry(self, event: PlatformEvent) -> bool:
        """Whether the original is kept only until its retry's write confirms."""
        return is_ordinary(event) and self.owner.delivery.keeps_original(
            message_id(event)
        )

    def release(self, identities: Iterable[str]) -> list[str]:
        """Drop the retained originals no turn will run; return their ids."""
        released = set(identities)
        dropped = self.pending.remove(
            lambda event: delivery_id(event) in released and not self.holds_retry(event)
        )
        return [identity for event in dropped if (identity := delivery_id(event))]

    def receive(self, event: PlatformEvent) -> None:
        if (
            isinstance(event, MessageEvent)
            and self.first_ws_id is None
            and not self.sync_complete
        ):
            self.first_ws_id = delivery_id(event)
        self.pending.add(event)

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
            self.queue.put_nowait(live_request)

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

    async def offer_control(
        self, identity: str, handle: Callable[[], Awaitable[bool]]
    ) -> bool:
        """Offer a control to its handler once per pass and revision; a decline sticks."""
        if self.declined(identity):
            return False
        active = self.current_pass
        revision = self.owner.control_revision
        handled = await handle()
        if not handled:
            self.record_decline(active, revision, identity)
        return handled

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

    @staticmethod
    def release_expired_deadline_cancel(wait: asyncio.Timeout | None) -> None:
        """Drop the deadline's leftover cancel after the wait returned an event.

        ``Timeout`` consumes its cancel only when the wait raises. A deadline
        that lands as an event arrives leaves one cancel pending; clearing
        that one leaves any real cancel in place.
        """
        if wait is None or not wait.expired():
            return
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            task.uncancel()

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
                deadline_wait: asyncio.Timeout | None = None
                try:
                    # A queued event is taken without arming the deadline.
                    # The empty-queue wait stays on the owner task so shutdown
                    # cannot be lost in a child queue-wait task on Windows.
                    if self.queue.empty():
                        async with asyncio.timeout_at(deadline) as deadline_wait:
                            self.queue_wait = deadline_wait
                            event = await self.queue.get()
                    else:
                        event = self.queue.get_nowait()
                except TimeoutError:
                    continue
                finally:
                    self.queue_wait = None
                self.release_expired_deadline_cancel(deadline_wait)
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

    async def retry_settlements(self) -> None:
        for identity in self.owner.claims.pending_ack_ids(self.owner.room_id):
            retry = self.owner.delivery.keeps_original(identity)
            await self.owner.delivery.retry_settlement(identity)
            if (
                retry
                and not self.owner.delivery.keeps_original(identity)
                and self.current_pass is not None
            ):
                self.current_pass.resumed_retries.add(identity)

    async def run_pass(self, *, recovery: bool = False) -> ResyncOutcome:
        if recovery:
            self.owner.delivery.lift_write_backoff()
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
                await self.retry_settlements()
                active.ordinary_held = self.deferred_id is not None and not recovery
                outcome = ResyncOutcome.PROGRESS
                while outcome is ResyncOutcome.PROGRESS and not self.owner.is_closing:
                    if not recovery and self.preempted():
                        return ResyncOutcome.BLOCKED
                    outcome = await self.advance(active)
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
                    # A skipped /next head is still outstanding, so this is not idle.
                    empty_room = (
                        outcome is ResyncOutcome.EMPTY
                        and not self.handled_work
                        and not self.next_fallback
                    )
                    self.schedule.complete(
                        asyncio.get_running_loop().time(),
                        empty=empty_room,
                        stopped=self.owner.is_stopped,
                    )

    def preempted(self) -> bool:
        """Whether a live pass must yield to a requested or due resync."""
        due_at = self.schedule.due_at
        return self.pending_resync is not None or (
            due_at is not None and due_at <= asyncio.get_running_loop().time()
        )

    async def advance(self, active: DiscoveryPass) -> ResyncOutcome:
        """Take the next step; invalidation ends the pass, housekeeping keeps it going."""
        outcome = await self.take_step(active)
        if active.invalidated:
            return ResyncOutcome.BLOCKED
        if outcome is ResyncOutcome.STOPPED and self.housekeeping() is not None:
            return ResyncOutcome.PROGRESS
        return outcome

    async def take_step(self, active: DiscoveryPass) -> ResyncOutcome:
        """Housekeeping before held or stopped input, then controls, then ordinary work."""
        gated = self.ordinary_gated(active)
        housekeeping = self.housekeeping() if gated or self.owner.is_stopped else None
        if housekeeping is not None:
            return (await self.dispatch_local(housekeeping)).scheduling
        if self.owner.is_stopped:
            return ResyncOutcome.STOPPED
        if gated:
            return await self.priority_step(active)
        outcome = await self.step(active)
        if outcome in {ResyncOutcome.BLOCKED, ResyncOutcome.RETRY} and self.deferred:
            active.ordinary_held = True
            return ResyncOutcome.PROGRESS
        return outcome

    async def resume_from_gate(self, active: DiscoveryPass) -> ResyncOutcome | None:
        revision = self.owner.control_revision
        row = await self.owner.link.get_next_message(self.owner.room_id)
        self.absorb()
        if revision != self.owner.control_revision:
            return ResyncOutcome.BLOCKED
        if row is None:
            return ResyncOutcome.STOPPED
        if self.owner.delivery.eligibility(Candidate.listed(row)) not in {
            "runnable",
            "pending",
            "inflight",
        }:
            return ResyncOutcome.STOPPED
        self.owner.resume_from_gate()
        active.gate_message = row
        return None

    def candidates(self, messages: Iterable[PlatformMessage]) -> list[Candidate]:
        """The page's rows merged with every retained original, each classified."""
        rest = [Candidate.listed(row) for row in messages]
        retained = [
            Candidate.retained(event) for event in self.pending if is_ordinary(event)
        ]
        return [self.classify(candidate) for candidate in merge(rest, retained)]

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

    def next_local(self) -> PlatformEvent | None:
        """The first event owing no delivery, past originals held only for a retry.

        A held original never runs again until its write confirms, so an event
        queued behind it must not wait for it.
        """
        for event in self.pending:
            if self.holds_retry(event):
                continue
            return None if is_ordinary(event) else event
        return None

    def local_segment(self) -> list[MessageEvent]:
        segment: list[MessageEvent] = []
        for event in self.pending:
            if not is_ordinary(event):
                break
            segment.append(event)
        return segment

    def local_predecessor(self, identity: str) -> PlatformEvent | None:
        """Keep a known original behind the local boundary it arrived after."""
        head: PlatformEvent | None = None
        boundary = False
        for event in self.pending:
            if self.holds_retry(event):
                continue
            if delivery_id(event) == identity:
                return head if boundary else None
            if head is None:
                head = event
            if not is_ordinary(event):
                boundary = True
        return None

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
        released = self.release(skipped)
        if released:
            logger.debug(
                "Room %s: Releasing retained message(s) %s that no turn will run",
                self.owner.room_id,
                released,
            )
        return skipped

    async def step(self, active: DiscoveryPass) -> ResyncOutcome:
        self.absorb()
        if self.owner.is_stopped or self.owner.is_closing:
            return ResyncOutcome.STOPPED
        recovery = active.recovery or not self.sync_complete
        local = self.next_local()
        # A deferred head already holds its place ahead of any backlog read.
        if local is not None and (
            not recovery
            or (self.deferred_id is not None and delivery_id(local) == self.deferred_id)
        ):
            return (await self.dispatch_local(local)).scheduling
        read = await self.read_page(active, recovery)
        if isinstance(read, ResyncOutcome):
            return read
        if self.owner.is_stopped:
            return ResyncOutcome.STOPPED
        if (
            not read.rows
            and self.sync_complete
            and (local := self.next_local()) is not None
        ):
            return (await self.dispatch_local(local)).scheduling
        return await self.run_selection(active, read)

    async def read_page(
        self, active: DiscoveryPass, recovery: bool
    ) -> PageRead | ResyncOutcome:
        """The platform rows this step selects from, or the outcome ending it early."""
        if not recovery:
            return PageRead([], exhausted=True)
        revision = self.owner.control_revision
        if self.next_fallback:
            page = await self.owner.link.get_actionable_page(
                self.owner.room_id, cursor=self.scan_cursor
            )
            self.absorb()
            if revision != self.owner.control_revision:
                return ResyncOutcome.PROGRESS
            return PageRead(page.messages, page.next_cursor is None, page.next_cursor)
        row = active.gate_message
        active.gate_message = None
        if row is None:
            row = await self.owner.link.get_next_message(self.owner.room_id)
        self.absorb()
        if revision != self.owner.control_revision:
            return ResyncOutcome.PROGRESS
        if row is None:
            self.sync_complete = True
            active.recovery = False
            return PageRead([], exhausted=True)
        if self.classify(Candidate.listed(row)).disposition is Disposition.SKIP:
            self.next_fallback = True
            return ResyncOutcome.PROGRESS
        return PageRead([row], exhausted=False)

    async def run_selection(
        self, active: DiscoveryPass, read: PageRead
    ) -> ResyncOutcome:
        """Select the next turn from the page and the retained originals, and run it."""
        segment = self.local_segment()
        if not read.rows and self.sync_complete:
            candidates = [self.classify(Candidate.retained(event)) for event in segment]
        else:
            candidates = self.candidates(read.rows)
            candidates.sort(
                key=lambda candidate: candidate.message_id not in active.resumed_retries
            )
        frontier = MessageKey.last_of(read.rows)
        selection = select_prefix(
            candidates,
            listed={row.id for row in read.rows},
            cap=SINGLETON_TURN_SIZE,
            page_reaches=frontier,
            exhausted=read.exhausted,
            resumed=active.resumed_retries,
        )
        skipped = self.prune_skipped(candidates, frontier, exhausted=read.exhausted)
        next_input = selection.members[0] if selection.members else selection.at
        if next_input is not None and selection.stop is not Stop.FRONTIER:
            prior = self.local_predecessor(next_input.message_id)
            if prior is not None:
                if not is_ordinary(prior):
                    return (await self.dispatch_local(prior)).scheduling
                member = next(c for c in candidates if c.event is prior)
                return await self.dispatch_selection(active, (member,))
        if selection.members:
            return await self.dispatch_selection(active, selection.members)
        if selection.stop is Stop.HELD and selection.at is not None:
            logger.debug(
                "Room %s: Message %s is held by another claim; deferring behind it",
                self.owner.room_id,
                selection.at.message_id,
            )
            self.defer(selection.at.message_id)
            return ResyncOutcome.BLOCKED
        return self.advance_scan(active, read, skipped, segment)

    async def dispatch_selection(
        self, active: DiscoveryPass, members: tuple[Candidate, ...]
    ) -> ResyncOutcome:
        result = (await self.dispatch(members)).scheduling
        if result is ResyncOutcome.PROGRESS:
            self.next_fallback = False
            self.scan_cursor = None
            if any(c.message_id == self.first_ws_id for c in members):
                self.sync_complete = True
                self.first_ws_id = None
                if self.schedule.due_at is None:
                    active.recovery = False
        return result

    def advance_scan(
        self,
        active: DiscoveryPass,
        read: PageRead,
        skipped: set[str],
        segment: list[MessageEvent],
    ) -> ResyncOutcome:
        """Nothing was selected: move to the next page, or conclude the scan."""
        if read.next_cursor is not None:
            self.scan_cursor = read.next_cursor
            return ResyncOutcome.PROGRESS
        self.scan_cursor = None
        if read.exhausted:
            self.sync_complete = True
            active.recovery = False
        if skipped and any(
            delivery_id(event) in skipped and not self.holds_retry(event)
            for event in segment
        ):
            return ResyncOutcome.PROGRESS
        if self.sync_complete and self.next_local() is not None:
            return ResyncOutcome.PROGRESS
        return ResyncOutcome.EMPTY

    async def dispatch(
        self, members: tuple[Candidate, ...], *, priority: bool = False
    ) -> DispatchResult:
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
            key = DispatchKey.of(members, revision, priority=priority)
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
            case TurnOutcome.ADVANCED | TurnOutcome.ABSENT:
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
        if outcome is TurnOutcome.ADVANCED:
            self.pending.remove(lambda item: item is event)
        elif not isinstance(event, MessageEvent):
            # A roster event the room could not begin waits for the next wake.
            return DispatchResult(ResyncOutcome.RETRY, outcome)
        identities = (message_id(event),) if is_synthetic(event) else ()
        return self.after_delivery(outcome, identities, revision, priority=False)

    async def priority_step(self, active: DiscoveryPass) -> ResyncOutcome:
        if not self.owner.handles_message_control:
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
        candidates = self.candidates(active.control_page.messages)
        frontier = MessageKey.last_of(active.control_page.messages)
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
                    and not self.owner.delivery.keeps_original(candidate.message_id)
                ):
                    self.defer(None)
            elif candidate.disposition is Disposition.CONTROL and not self.declined(
                candidate.message_id
            ):
                key = DispatchKey.of((candidate,), revision, priority=True)
                if key not in active.selections:
                    result = await self.dispatch((candidate,), priority=True)
                    if result.turn is TurnOutcome.ADVANCED:
                        active.ordinary_held = False
                        active.selections = {
                            selected
                            for selected in active.selections
                            if selected.priority
                        }
                    return (
                        ResyncOutcome.STOPPED
                        if result.scheduling is ResyncOutcome.STOPPED
                        else ResyncOutcome.PROGRESS
                    )
        return ResyncOutcome.BLOCKED if self.deferred else ResyncOutcome.PROGRESS
