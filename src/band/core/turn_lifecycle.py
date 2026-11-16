"""One cancellation owner per concrete turn task, and per backend turn.

``TurnTask`` delivers cancellation and drains one handler or SDK-managed worker
within a single absolute cleanup grace. Adapters whose manual-approval turn runs
detached bind that worker to the originating turn scope and share
``ApprovalInterruptMixin`` for interrupts. ``BackendTurns`` is the remote half:
it stops the turn an external backend is still running.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from inspect import getattr_static, ismethod
from typing import TYPE_CHECKING, Annotated, Any, Generic, TypeVar, cast

from band_sdk_core import AgentFailure, backend_stop_unconfirmed_message
from pydantic import Field, NonNegativeFloat

from band.client.streaming import ControlMode
from band.core.exceptions import (
    CleanupIncompleteError,
    InputNotAccepted,
    ProviderUnavailableError,
)
from band.core.protocols import AgentToolsProtocol
from band.core.retention import Residency, RetainedRoom
from band.core.retention.cleanup import await_owned, await_owned_result, spawn_owned
from band.core.turn import judge_detached_turn

logger = logging.getLogger(__name__)

CYCLE_CANCEL_GRACE_SECONDS = 1.0
# How long one backend stop may run, including any wait for the start call
# whose turn it stops, before it is abandoned.
BACKEND_CANCEL_TIMEOUT_SECONDS = 5.0
# How long a room's final disposal lets its accepted turn wind down on its own
# before cancelling it.
TURN_SETTLE_GRACE_S = 10.0
# The adapter config field that sets a room's settle grace. Each config assigns
# TURN_SETTLE_GRACE_S itself: type checkers miss a default inside Annotated.
TurnSettleTimeout = Annotated[
    NonNegativeFloat,
    Field(
        description=(
            "Seconds a leave or stop lets the room's turn wind down on its own "
            "before cancelling it."
        ),
    ),
]

H = TypeVar("H")

# Room-safe report when bounded recovery cannot reopen admission.
PROVIDER_UNAVAILABLE_MESSAGE = (
    "The provider for this room is unavailable until it is safely recovered. "
    "Queued and new input fails without invoking the provider."
)
# Shared refusal while a prior stop still owns admission.
BACKEND_TURN_NOT_READY_MESSAGE = "The previous backend turn is not ready"


class BackendStopNotice(StrEnum):
    """Closed vocabulary for one-shot room notices about an unconfirmed stop."""

    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


# Latch deferred originals into terminal delivery failure before readiness wakes.
FailureLatch = Callable[[str, frozenset[str]], None]


def terminal_deadline() -> float:
    """The absolute end of a cleanup grace that starts now."""
    return asyncio.get_running_loop().time() + CYCLE_CANCEL_GRACE_SECONDS


async def finished_within_grace(task: asyncio.Task[Any]) -> bool:
    """Wait up to one cleanup grace for ``task`` without cancelling it."""
    await asyncio.wait({task}, timeout=CYCLE_CANCEL_GRACE_SECONDS)
    return task.done()


def retire_provider_timeout(timer: asyncio.Timeout) -> bool:
    """Disarm an entered provider timer; report whether it already fired."""
    if timer.expired():
        return True
    timer.reschedule(None)
    return False


class TaskRole(StrEnum):
    SCOPE = "scope"
    RESOURCE = "resource"


class TurnTask:
    """Own cancellation and bounded draining for one concrete turn task."""

    def __init__(
        self,
        task: asyncio.Task[Any],
        *,
        role: TaskRole = TaskRole.SCOPE,
        request_stop: Callable[[], None] | None = None,
    ) -> None:
        self.task = task
        self.role = role
        self._request_stop = request_stop
        if task.done():
            self._retrieve_result(task)
        else:
            task.add_done_callback(self._retrieve_result)
        self.failure: Exception | None = None
        self.deadline: float | None = None
        self._cancel_delivered = False
        self._grace_expired = False
        self._timer: asyncio.Timeout | None = None
        self._cleanup_timer = False
        self._on_revoked: Callable[[], None] | None = None

    @property
    def authoritative(self) -> bool:
        """Whether this task may still perform effects for its captured origin."""
        return self.deadline is None and not self.task.done()

    def fail(self, error: Exception, *, cooperative: bool = False) -> None:
        """Revoke for provider failure without replacing an earlier cancellation."""
        if not self.authoritative:
            return
        self.failure = error
        self.terminate(cooperative=cooperative)

    @staticmethod
    def _retrieve_result(task: asyncio.Task[Any]) -> None:
        if not task.cancelled():
            task.exception()

    def observe_revocation(self, callback: Callable[[], None]) -> None:
        self._on_revoked = callback
        if self.deadline is not None:
            callback()

    def terminate(
        self, *, deadline: float | None = None, cooperative: bool = False
    ) -> None:
        """Start the cleanup grace once; later requests never renew it."""
        if self.deadline is not None:
            return
        bound = deadline if deadline is not None else terminal_deadline()
        self.deadline = self._release_timer(bound)
        if self._on_revoked is not None:
            self._on_revoked()
        # The owner enforces its own deadline: a detached worker may have no drainer.
        asyncio.get_running_loop().call_at(self.deadline, self._expire)
        if not cooperative:
            self._cancel()

    def retry(self, *, deadline: float) -> None:
        """Cancel again within a fresh grace once the previous grace has expired.

        A task that swallowed its cancel would otherwise stay unfinished on
        every cleanup retry; within an unexpired grace this is ``terminate``.
        """
        if not self._grace_expired:
            self.terminate(deadline=deadline)
            return
        self.deadline = deadline
        self._grace_expired = False
        self._cancel_delivered = False
        asyncio.get_running_loop().call_at(deadline, self._expire)
        self._cancel()

    def _expire(self) -> None:
        # The loop's own timer marks expiry: it can fire up to one clock
        # resolution before loop.time() reaches the deadline.
        self._grace_expired = True
        self._cancel()

    def _release_timer(self, bound: float) -> float:
        """Keep an attached provider timer from cancelling terminal cleanup."""
        timer = self._timer
        if timer is None:
            return bound
        if self._cleanup_timer and (timer_bound := timer.when()) is not None:
            bound = min(bound, timer_bound)
        if retire_provider_timeout(timer):
            self._cancel_delivered = True
        return bound

    def _cancel(self) -> None:
        if not self._cancel_delivered and not self.task.done():
            self._cancel_delivered = True
            if self._request_stop is None:
                self.task.cancel()
            else:
                self._request_stop()

    async def drain(self) -> bool:
        """Wait within the original terminal grace without cancelling a waiter."""
        if self.task.done():
            return True
        if self.deadline is None:
            raise RuntimeError("Turn draining requires termination")
        remaining = max(0.0, self.deadline - asyncio.get_running_loop().time())
        await asyncio.wait({self.task}, timeout=remaining)
        return self.task.done()

    @asynccontextmanager
    async def timed(
        self, timeout: float | None, *, cleanup: bool = False
    ) -> AsyncIterator[asyncio.Timeout]:
        """Coordinate an existing provider timer with terminal cancellation."""
        if self.deadline is not None:
            raise asyncio.CancelledError
        if self._timer is not None or asyncio.current_task() is not self.task:
            raise RuntimeError("A turn timer must run in its owner, without nesting")
        timer = asyncio.timeout(timeout)
        try:
            async with timer:
                self._timer = timer
                self._cleanup_timer = cleanup
                yield timer
        finally:
            self._timer = None
            self._cleanup_timer = False
            if self.deadline is not None:
                if self.failure is not None:
                    raise self.failure
                raise asyncio.CancelledError


async def drain_turn_tasks(owners: Iterable[TurnTask]) -> None:
    """Terminate captured tasks and drain them within one shared cleanup grace."""
    captured = tuple(owners)
    deadline = terminal_deadline()
    for owner in captured:
        owner.terminate(deadline=deadline)
    if captured:
        await asyncio.wait(
            {owner.task for owner in captured},
            timeout=max(0, deadline - asyncio.get_running_loop().time()),
        )


def start_releasable_turn(
    retained: RetainedRoom,
    operation: Callable[[], Coroutine[Any, Any, None]],
    *,
    tools: AgentToolsProtocol,
    room_id: str,
) -> TurnTask:
    """Start a turn that may be released before it ends as the room's owned work.

    The turn is bound to its delivery's scope, so it keeps that delivery's
    authority to post after the release until it actually ends. A released
    turn cancelled by its room's final disposal is judged then, since nothing
    else answers for its message; an interrupt ends it on purpose.
    """

    async def run() -> None:
        try:
            await operation()
        except asyncio.CancelledError:
            if retained.closing:
                await judge_detached_turn(tools, room_id=room_id)
            raise

    return bind_turn_task(tools, retained.start_owned(run))


def bind_turn_task(
    tools: object,
    task: asyncio.Task[Any],
    *,
    role: TaskRole = TaskRole.SCOPE,
    request_stop: Callable[[], None] | None = None,
) -> TurnTask:
    """Bind SDK-owned work when tools expose a runtime scope."""
    owner = TurnTask(task, role=role, request_stop=request_stop)
    register = getattr(tools, "register_turn_task", None)
    if callable(register):
        register(owner)
    return owner


def resource_tools(tools: AgentToolsProtocol, owner: TurnTask) -> AgentToolsProtocol:
    """Capture a resource view when the tools declare runtime ownership support."""
    if getattr_static(tools, "_for_resource", None) is None:
        return tools
    capture = getattr(tools, "_for_resource", None)
    if callable(capture):
        return capture(owner)
    return tools


@dataclass(eq=False)
class BackendTurn(Generic[H]):
    """One room's backend turn: its handle once known, and whether to stop it."""

    handle: H | None = None
    stop_requested: bool = False
    # Captured before tool rebinding so late work cannot follow a successor.
    generation: int | None = None


@dataclass
class UnconfirmedStop(Generic[H]):
    """Keep admission blocked until matching evidence retires this operation."""

    handle: H | None
    ready: asyncio.Future[None]
    room_id: str
    # One report per distinct notice so a blocked deferral cannot suppress the
    # later terminal unavailable message (or the reverse).
    reported: set[BackendStopNotice] = field(default_factory=set)
    recovery: tuple[RetainedRoom, int] | None = None
    release_callback: Callable[[], None] | None = None
    # Terminal admission latch from mark_unavailable (any path); wakes readiness.
    unavailable: bool = False
    # Originals deferred on this stop; latched terminal before the wake.
    waiting: set[str] = field(default_factory=set)

    def detach_release(self) -> None:
        """Remove the recovery observation when this stop loses authority."""
        if self.recovery is not None and self.release_callback is not None:
            retained, _ = self.recovery
            retained.remove_release_callback(self.release_callback)
        self.release_callback = None


class BackendTurns(Generic[H]):
    """Each room's in-flight backend turn, and the one way to stop it.

    The adapter supplies only the cancel call for its handle; the rules live
    here. A turn is stopped at most once, never by awaiting on the caller, and
    within ``BACKEND_CANCEL_TIMEOUT_SECONDS``. The room's next turn and its
    teardown wait for that stop.

    A deadline requests abandonment only. Reuse requires matching provider
    evidence and retirement of effectful local callbacks. When bounded recovery
    fails, the room becomes unavailable: deferred and new model input finishes
    with a terminal, non-retrying failure before readiness wakes.

    Placement:

    - The task that owns the backend call holds ``running``, so a turn that
      outlives ``on_message`` (released for approval) stays registered.
    - ``running`` wraps outside any timeout scope: a timeout reaches it as
      ``TimeoutError``, and the adapter's timeout branch awaits ``stop``
      before its own cleanup.
    - Where the backend is acquired before the owning task enters ``running``,
      the adapter awaits ``settled`` first.
    - Where the call that starts the turn also reveals its handle, the owner
      awaits it through ``started``, so a stop asked for mid-call still lands.
    - The handle carries the turn's own state the cancel needs; the cancel
      never reads per-turn ambient context such as a ``ContextVar``.
    """

    def __init__(
        self,
        cancel: Callable[[H], Awaitable[None]],
        *,
        provider: str,
        requires_confirmation: bool = False,
    ) -> None:
        self._cancel = cancel
        self._provider = provider
        self._turns: dict[str, BackendTurn[H]] = {}
        self._stopping: dict[str, asyncio.Task[None]] = {}
        self._requires_confirmation = requires_confirmation
        self._unconfirmed: dict[str, UnconfirmedStop[H]] = {}
        # A bound-method latch belongs to a room that can retire before this
        # adapter-lifetime owner, so it is held weakly.
        self._failure_latches: list[
            weakref.WeakMethod[FailureLatch] | FailureLatch
        ] = []
        self._by_ready: dict[asyncio.Future[None], UnconfirmedStop[H]] = {}

    def add_failure_latch(self, latch: FailureLatch) -> None:
        """Register delivery's terminal latch for unavailable deferred originals."""
        held: weakref.WeakMethod[FailureLatch] | FailureLatch = latch
        if ismethod(latch):
            held = weakref.WeakMethod(cast("Any", latch))
        if held not in self._failure_latches:
            self._failure_latches.append(held)

    def _live_failure_latches(self) -> list[FailureLatch]:
        """The registered latches, forgetting those whose room is gone."""
        live: list[FailureLatch] = []
        kept: list[weakref.WeakMethod[FailureLatch] | FailureLatch] = []
        for held in self._failure_latches:
            latch = held() if isinstance(held, weakref.WeakMethod) else held
            if latch is not None:
                live.append(latch)
                kept.append(held)
        self._failure_latches = kept
        return live

    def note_waiting(self, room_id: str, message_id: str) -> None:
        """Record an original deferred on this room's unconfirmed stop."""
        stop = self._unconfirmed.get(room_id)
        if stop is not None:
            self._record_waiting(stop, message_id)

    def note_waiting_on(self, ready: asyncio.Future[None], message_id: str) -> None:
        """Record a deferred original from its readiness future."""
        stop = self._by_ready.get(ready)
        if stop is not None:
            self._record_waiting(stop, message_id)

    def _record_waiting(self, stop: UnconfirmedStop[H], message_id: str) -> None:
        stop.waiting.add(message_id)
        if stop.unavailable:
            self._latch_waiting(stop.room_id, frozenset({message_id}))

    def abandon_start(self, room_id: str) -> None:
        """Retain uncertainty when setup failed after possible provider acceptance."""
        self._unconfirmed_stop(room_id, None)
        self.mark_unavailable(room_id)

    def abandon_start_wait(self, room_id: str, start: Awaitable[H | None]) -> None:
        """Caller abandoned waiting for ``start``; late identity is cleanup-only.

        Does not latch unavailable: matching cancel/settlement evidence still
        reopens the room, and only failed recovery marks it unavailable.
        """
        call = asyncio.ensure_future(start)
        stop = self._unconfirmed_stop(room_id, None)
        if not self.stopping(room_id):
            self._spawn_stop(
                room_id,
                self._stop_when_started(room_id, call, stop),
                cancel_on_timeout=False,
            )

    def mark_unavailable(self, room_id: str) -> frozenset[str]:
        """Latch terminal failure for waiting originals, then wake readiness."""
        stop = self._unconfirmed.get(room_id)
        if stop is None:
            return frozenset()
        failed = frozenset(stop.waiting)
        if stop.unavailable:
            return failed
        stop.unavailable = True
        logger.warning(
            "Marking %s room unavailable until matching recovery evidence",
            self._provider,
        )
        try:
            self._latch_waiting(room_id, failed)
        finally:
            if not stop.ready.done():
                stop.ready.set_exception(self._unavailable_failure())
                stop.ready.exception()
        return failed

    def _unavailable_failure(self) -> ProviderUnavailableError:
        return ProviderUnavailableError(
            AgentFailure(self._provider, PROVIDER_UNAVAILABLE_MESSAGE)
        )

    def _latch_waiting(self, room_id: str, identities: frozenset[str]) -> None:
        """Notify delivery latches; one latch failure must not starve the rest."""
        for latch in self._live_failure_latches():
            try:
                latch(room_id, identities)
            except Exception as error:
                logger.warning(
                    "Failure latch for room %s raised (%s)",
                    room_id,
                    type(error).__name__,
                    exc_info=error,
                )

    def unavailable(self, room_id: str) -> bool:
        stop = self._unconfirmed.get(room_id)
        return stop is not None and stop.unavailable

    @asynccontextmanager
    async def running(
        self, room_id: str, handle: H | None = None, *, generation: int | None = None
    ) -> AsyncIterator[None]:
        """Register the room's turn; a cancelled exit stops its backend.

        An unconfirmed or unavailable prior stop is refused here rather than
        waited on forever under a caller's lock. Callers that need the blocked
        notice use ``require_admission`` first.
        """
        await self.settled(room_id)
        prior = self._unconfirmed.get(room_id)
        if prior is not None and prior.unavailable:
            raise self._unavailable_failure()
        if prior is not None and not prior.ready.done():
            raise InputNotAccepted(BACKEND_TURN_NOT_READY_MESSAGE, ready=prior.ready)
        turn = BackendTurn(handle, generation=generation)
        self._turns[room_id] = turn
        try:
            yield
        except asyncio.CancelledError:
            self.cancel(room_id)
            raise
        finally:
            if self._turns.get(room_id) is turn:
                del self._turns[room_id]
            if (
                turn.stop_requested
                and turn.handle is None
                and not self.stopping(room_id)
            ):
                logger.info(
                    "%s turn in room %s ended before its backend revealed it; "
                    "no backend stop was sent",
                    self._provider,
                    room_id,
                )

    async def started(self, room_id: str, start: Awaitable[H | None]) -> H | None:
        """Await the call that starts the room's backend turn and bind the
        handle it reveals.

        The call outlives a cancelled owner, so a turn the backend already
        accepted is still stopped once its handle arrives. A late handle after
        abandonment feeds cleanup only. Prefer ``start_within`` when a
        transition budget owns the wait.
        """
        call = asyncio.ensure_future(start)
        try:
            handle = await asyncio.shield(call)
        except asyncio.CancelledError:
            self.abandon_start_wait(room_id, call)
            raise
        if handle is not None:
            self.bind(room_id, handle)
        return handle

    async def start_within(
        self,
        room_id: str,
        start: Awaitable[H | None],
        *,
        timeout: float | None,
        label: str,
    ) -> H | None:
        """Await ``start`` under a transition budget and bind its handle.

        Expiry or host cancel abandons the wait through ``abandon_start_wait``
        so late identity is cleanup-only; the start awaitable stays owned for
        that cleanup. One owner covers both ``TimeoutError`` and
        ``CancelledError`` — adapters must not wrap ``started`` themselves.
        """
        call = asyncio.ensure_future(start)
        try:
            handle = await await_owned_result(call, timeout=timeout, label=label)
        except (TimeoutError, asyncio.CancelledError):
            self.abandon_start_wait(room_id, call)
            raise
        if handle is not None:
            self.bind(room_id, handle)
        return handle

    def bind(self, room_id: str, handle: H) -> None:
        """Attach the handle the backend revealed; a stop already asked for runs now."""
        turn = self._turns.get(room_id)
        if turn is None or turn.handle is not None:
            return
        turn.handle = handle
        if turn.stop_requested:
            self._stop(room_id, handle)

    def cancel(self, room_id: str) -> None:
        """Start stopping the room's backend turn, once, without waiting for it."""
        turn = self._turns.get(room_id)
        if turn is None or turn.stop_requested:
            return
        turn.stop_requested = True
        if turn.handle is not None:
            self._stop(room_id, turn.handle)

    async def stop(self, room_id: str) -> None:
        """Stop the room's backend turn and wait until that stop settles."""
        self.cancel(room_id)
        await self.settled(room_id)

    def stopping(self, room_id: str) -> bool:
        """Whether the room's backend stop is still in flight."""
        return room_id in self._stopping

    def blocker(self, room_id: str) -> asyncio.Future[None] | None:
        """Return the stop whose settlement permits another admission check."""
        task = self._stopping.get(room_id)
        if task is not None and not task.done():
            return task
        stop = self._unconfirmed.get(room_id)
        if stop is None or stop.unavailable:
            return None
        return stop.ready

    def unconfirmed_handle(self, room_id: str) -> H | None:
        stop = self._unconfirmed.get(room_id)
        return stop.handle if stop is not None else None

    def confirm_stopped(self, room_id: str, handle: H | None) -> None:
        """Release only the stop belonging to this concrete provider operation."""
        turn = self._turns.get(room_id)
        if turn is not None and turn.handle is handle:
            del self._turns[room_id]
        stop = self._unconfirmed.get(room_id)
        if stop is not None and stop.handle is handle:
            del self._unconfirmed[room_id]
            self._forget_ready(stop)
            stop.detach_release()
            if not stop.ready.done():
                stop.ready.set_result(None)

    def recovering(
        self, room_id: str, handle: H, retained: RetainedRoom, *, generation: int
    ) -> None:
        """Keep a stopped process's existing release owner in the admission path."""
        stop = self._unconfirmed_stop(room_id, handle)
        if stop.handle is not handle:
            return
        stop.detach_release()
        stop.recovery = retained, generation
        stop.release_callback = partial(
            self._released, room_id, stop, retained, generation
        )
        retained.add_release_callback(stop.release_callback, generation=generation)
        retained.recover(generation=generation, reason="Backend turn interrupted")

    def _released(
        self,
        room_id: str,
        stop: UnconfirmedStop[H],
        retained: RetainedRoom,
        generation: int,
    ) -> None:
        if self._unconfirmed.get(room_id) is stop and stop.recovery == (
            retained,
            generation,
        ):
            self.confirm_stopped(room_id, stop.handle)

    def confirm_disposed(self, room_id: str) -> None:
        """Forget recovery only after its owner confirms the final physical release."""
        stop = self._unconfirmed.get(room_id)
        if stop is None or stop.recovery is None:
            return
        retained, _ = stop.recovery
        if retained.snapshot.state in (Residency.DISPOSING, Residency.CLOSED):
            # Final disposal clears admission even after an unavailable latch;
            # deferred originals already carry their terminal delivery outcome.
            self.confirm_stopped(room_id, stop.handle)

    async def ready_to_forget(self, room_id: str) -> bool:
        """Wait for any in-flight stop; False while any unconfirmed stop remains."""
        await self.settled(room_id)
        return room_id not in self._unconfirmed

    async def require_admission(self, room_id: str, tools: AgentToolsProtocol) -> None:
        """Refuse before execution while a stop is pending, unconfirmed, or unavailable."""
        stop = self._unconfirmed.get(room_id)
        if stop is not None and stop.unavailable:
            raise await self._unavailable_error(tools, stop)
        if (
            stop is not None
            and stop.recovery is not None
            and not self.stopping(room_id)
        ):
            retained, generation = stop.recovery
            if retained.snapshot.state != Residency.CLOSED:
                retained.require_admission()
            try:
                await retained.wait_released(generation=generation)
            except CleanupIncompleteError as error:
                # A finished release typed this way is a real failure; a still-
                # running or enrolled wait leaves recovery owned for retry.
                if retained.snapshot.error is not None:
                    raise await self._fail_recovery(tools, stop, error) from error
                logger.warning(
                    "Backend recovery remains blocked (%s)",
                    type(error).__name__,
                )
            except Exception as error:
                raise await self._fail_recovery(tools, stop, error) from error
            else:
                self.confirm_stopped(room_id, stop.handle)
        blocker = self.blocker(room_id)
        stop = self._unconfirmed.get(room_id)
        if blocker is None:
            if stop is not None and stop.unavailable:
                raise await self._unavailable_error(tools, stop)
            return
        if stop is not None and blocker is stop.ready:
            await self._report_once(
                tools,
                stop,
                backend_stop_unconfirmed_message(),
                BackendStopNotice.BLOCKED,
            )
        raise InputNotAccepted(BACKEND_TURN_NOT_READY_MESSAGE, ready=blocker)

    async def _fail_recovery(
        self,
        tools: AgentToolsProtocol,
        stop: UnconfirmedStop[H],
        error: BaseException,
    ) -> ProviderUnavailableError:
        """Mark the room unavailable after a failed release and report once."""
        logger.warning(
            "Backend recovery failed (%s)",
            type(error).__name__,
        )
        self.mark_unavailable(stop.room_id)
        return await self._unavailable_error(tools, stop)

    async def _unavailable_error(
        self, tools: AgentToolsProtocol, stop: UnconfirmedStop[H]
    ) -> ProviderUnavailableError:
        """Report the room unavailable once and return the refusal to raise."""
        await self._report_once(
            tools, stop, PROVIDER_UNAVAILABLE_MESSAGE, BackendStopNotice.UNAVAILABLE
        )
        return self._unavailable_failure()

    async def _report_once(
        self,
        tools: AgentToolsProtocol,
        stop: UnconfirmedStop[H],
        message: str,
        what: BackendStopNotice,
    ) -> None:
        """Tell the room once per notice why this stop blocks it; never releases it."""
        from band.runtime.tools.schema import is_failed_tool_output  # noqa: PLC0415 -- runtime imports this lifecycle owner

        if what in stop.reported:
            return
        try:
            result = await tools.send_failure(AgentFailure(self._provider, message))
        except Exception as error:  # noqa: BLE001 -- reporting cannot release blocked work
            logger.warning(
                "Could not report %s backend (%s)", what, type(error).__name__
            )
        else:
            if not is_failed_tool_output(result):
                stop.reported.add(what)

    def active_handle(self, room_id: str) -> H | None:
        """Return the running turn's handle until stopping is requested."""
        turn = self._turns.get(room_id)
        return None if turn is None or turn.stop_requested else turn.handle

    async def settled(self, room_id: str, *, timeout: float | None = None) -> None:
        """Wait until the room's backend stop has been sent or timed out, or
        until ``timeout`` passes."""
        if (task := self._stopping.get(room_id)) is not None:
            await asyncio.wait({task}, timeout=timeout)

    async def drain(self) -> None:
        """Wait until every room's backend stop has been sent or timed out."""
        if self._stopping:
            await asyncio.wait(set(self._stopping.values()))

    def _stop(self, room_id: str, handle: H) -> None:
        stop = self._unconfirmed_stop(room_id, handle)
        self._spawn_stop(room_id, self._cancel_and_confirm(room_id, handle, stop))

    async def _cancel_and_confirm(
        self, room_id: str, handle: H, stop: UnconfirmedStop[H]
    ) -> None:
        await self._cancel(handle)
        if not self._requires_confirmation and self._unconfirmed.get(room_id) is stop:
            self.confirm_stopped(room_id, handle)

    def _unconfirmed_stop(self, room_id: str, handle: H | None) -> UnconfirmedStop[H]:
        stop = self._unconfirmed.get(room_id)
        if stop is None:
            ready = asyncio.get_running_loop().create_future()
            stop = self._unconfirmed[room_id] = UnconfirmedStop(
                handle, ready, room_id=room_id
            )
            self._by_ready[ready] = stop
        elif stop.handle is None:
            stop.handle = handle
        return stop

    def _forget_ready(self, stop: UnconfirmedStop[H]) -> None:
        self._by_ready.pop(stop.ready, None)
        for ready, held in list(self._by_ready.items()):
            if held is stop:
                self._by_ready.pop(ready, None)

    async def _stop_when_started(
        self,
        room_id: str,
        call: asyncio.Future[H | None],
        stop: UnconfirmedStop[H],
    ) -> None:
        try:
            handle = await call
        except Exception as error:  # noqa: BLE001 -- a start that failed left no turn to stop
            logger.debug(
                "%s turn start in room %s failed after its turn was stopped: %r",
                self._provider,
                room_id,
                error,
            )
            if self._unconfirmed.get(room_id) is stop and not stop.unavailable:
                # Never-accepted setup: deadlines must not latch unavailable.
                # Keep an unavailable latch already set by abandon_start.
                self.confirm_stopped(room_id, None)
            return
        if self._unconfirmed.get(room_id) is not stop:
            return
        if handle is None:
            self.confirm_stopped(room_id, None)
            return
        # Late identity after abandonment: cleanup only, never publish or rebind.
        self._unconfirmed_stop(room_id, handle)
        await self._cancel_and_confirm(room_id, handle, stop)

    def _spawn_stop(
        self,
        room_id: str,
        stop: Coroutine[Any, Any, None],
        *,
        cancel_on_timeout: bool = True,
    ) -> None:
        task = spawn_owned(
            self._wait_stop(room_id, stop, cancel_on_timeout=cancel_on_timeout),
            label=f"{self._provider} backend stop for room {room_id}",
        )
        self._stopping[room_id] = task
        if (unconfirmed := self._unconfirmed.get(room_id)) is not None:
            self._by_ready[task] = unconfirmed
        task.add_done_callback(partial(self._forget_stop, room_id))

    async def _wait_stop(
        self,
        room_id: str,
        stop: Coroutine[Any, Any, None],
        *,
        cancel_on_timeout: bool,
    ) -> None:
        operation = spawn_owned(
            stop, label=f"{self._provider} cancellation for room {room_id}"
        )
        try:
            if await await_owned({operation}, BACKEND_CANCEL_TIMEOUT_SECONDS):
                operation.result()
                return
            if cancel_on_timeout:
                operation.cancel()
        except BaseException:
            recovery = self._unconfirmed.get(room_id)
            if recovery is None or recovery.recovery is None:
                self.mark_unavailable(room_id)
            raise
        # Deadline abandons this wait only; confirmation or failed recovery
        # (including a cancel cancelled with no late evidence) still owns
        # whether the room becomes unavailable.
        raise TimeoutError("Backend stop exceeded its cancellation budget")

    def _forget_stop(self, room_id: str, task: asyncio.Task[None]) -> None:
        self._by_ready.pop(task, None)
        if self._stopping.get(room_id) is task:
            del self._stopping[room_id]


class ApprovalInterruptMixin:
    """Clear approvals and terminate the handler's worker on an interrupt.

    A room's leave and an adapter's stop belong to its residency owner, which
    refuses a closing room's work itself.
    """

    def _clear_pending_approvals_for_room(self, room_id: str) -> None: ...

    _turn_tasks: dict[str, TurnTask]

    if TYPE_CHECKING:

        @property
        def backend_turns(self) -> BackendTurns[Any] | None: ...

    def _turn_blocker(self, room_id: str) -> asyncio.Future[Any] | None:
        """The room's worker, or the stop of its backend turn, is unfinished."""
        owner = self._turn_tasks.get(room_id)
        if owner is not None and not owner.task.done():
            return owner.task
        return (
            self.backend_turns.blocker(room_id)
            if self.backend_turns is not None
            else None
        )

    def _turn_running(self, room_id: str) -> bool:
        return self._turn_blocker(room_id) is not None

    async def _admit_turn(
        self, room_id: str, provider: str, tools: AgentToolsProtocol | None = None
    ) -> None:
        """Refuse new input while the room's turn or backend stop still runs."""
        await self._join_backend_stop(room_id)
        if tools is not None and self.backend_turns is not None:
            await self.backend_turns.require_admission(room_id, tools)
        if (blocker := self._turn_blocker(room_id)) is not None:
            raise InputNotAccepted(
                f"The room already has an active {provider} turn", ready=blocker
            )

    async def _join_backend_stop(self, room_id: str) -> None:
        """Give the room's backend stop one cleanup grace to finish."""
        if self.backend_turns is not None:
            await self.backend_turns.settled(
                room_id, timeout=CYCLE_CANCEL_GRACE_SECONDS
            )

    async def _cancel_turn(self, room_id: str) -> None:
        owner = self._turn_tasks.get(room_id)
        if owner is not None:
            owner.terminate()
            await owner.drain()

    async def on_interrupt(self, room_id: str, mode: ControlMode) -> None:
        await super().on_interrupt(room_id, mode)  # type: ignore[misc]
        self._clear_pending_approvals_for_room(room_id)
        await self._cancel_turn(room_id)
