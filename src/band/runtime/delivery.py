"""Admission and durable settlement shared by every real message attempt."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterable, Iterator
from enum import Enum
from time import monotonic
from typing import Protocol

from band_sdk_core import (
    ClaimRegistry,
    DeliveryEligibility,
    ParkedReason,
    RetryTracker,
    is_self_echo,
)

from band.client.streaming import ControlMode, DeliveryStatus, MessageCreatedPayload
from band.core.exceptions import InputNotAccepted, RoomExecutionStoppedError
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TurnResultAlreadyReported,
)
from band.core.settlement import SettlementResult
from band.core.turn_lifecycle import TurnTask
from band.core.types import delivery_status_for_agent
from band.platform.event import MessageEvent
from band.platform.link import BandLink
from band.runtime.cycle import TurnScope
from band.runtime.messages import PlatformMessage
from band.runtime.sessionconfig import SessionConfig

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def observe_delivery_phase(
    phase: str, room_id: str, message_id: str | None = None
) -> Iterator[None]:
    """Record boundary completion separately from entry, without payloads."""
    started = monotonic()
    logger.debug(
        "Turn phase=%s state=started room=%s message=%s",
        phase,
        room_id,
        message_id,
    )
    outcome = "returned"
    try:
        yield
    except BaseException as error:
        outcome = (
            "cancelled"
            if isinstance(error, asyncio.CancelledError)
            else type(error).__name__
        )
        raise
    finally:
        logger.debug(
            "Turn phase=%s state=%s room=%s message=%s elapsed=%.6f",
            phase,
            outcome,
            room_id,
            message_id,
            monotonic() - started,
        )


def log_delivery_error(error: Exception, message: str, *args: object) -> None:
    """Log a failed turn; one whose failure already reached the room was logged
    where it was reported, so it stays out of ERROR alerting."""
    reported = isinstance(error, TurnResultAlreadyReported)
    level = logging.DEBUG if reported else logging.ERROR
    logger.log(level, message, *args, exc_info=not reported)


class TurnOutcome(Enum):
    """The scheduling decision following one delivery attempt."""

    ADVANCED = "advanced"
    PAUSED = "paused"
    RETRY_LATER = "retry_later"
    NOT_ACCEPTED = "not_accepted"
    DECLINED = "declined"


DEFERRED_OUTCOMES = frozenset({TurnOutcome.RETRY_LATER, TurnOutcome.NOT_ACCEPTED})


class ControlOutcome(Enum):
    CONSUMED = "consumed"
    DECLINED = "declined"
    DEFERRED = "deferred"


class DeliveryHost(Protocol):
    """Room effects whose ownership outlives an individual delivery operation."""

    @property
    def control_revision(self) -> int: ...

    @property
    def is_stopped(self) -> bool: ...

    @property
    def is_closing(self) -> bool: ...

    @property
    def context_hydrated(self) -> bool: ...

    def recognizes_delivery_control(self, event: MessageEvent) -> bool: ...

    def delivery_metadata(self, message_id: str) -> dict[str, object] | None: ...

    async def begin_delivery(self) -> TurnScope | None: ...

    async def prepare_delivery_context(self) -> None: ...

    def owns_delivery(self, scope: TurnScope, revision: int) -> bool: ...

    async def execute_delivery(self, scope: TurnScope, *, control: bool) -> bool: ...

    def defer_delivery(
        self, message_id: str | None, *, reason: InputNotAccepted
    ) -> None: ...

    async def report_delivery_failure(self, *, final: bool) -> None: ...

    def finish_delivery(self, scope: TurnScope) -> None: ...


class DeliveryLifecycle:
    """One eligibility, admission and settlement policy for all queue sources."""

    def __init__(
        self,
        room_id: str,
        link: BandLink,
        claims: ClaimRegistry,
        retry_tracker: RetryTracker,
        config: SessionConfig,
        host: DeliveryHost,
        agent_id: str | None,
    ) -> None:
        self.room_id = room_id
        self.link = link
        self.claims = claims
        self.retry_tracker = retry_tracker
        self.config = config
        self.host = host
        self.agent_id = agent_id
        self.retry_not_before: dict[str, float] = {}

    def _facts(
        self, message: MessageCreatedPayload | PlatformMessage
    ) -> dict[str, bool]:
        metadata = message.metadata
        status = delivery_status_for_agent(metadata, self.agent_id)
        cached = delivery_status_for_agent(
            self.host.delivery_metadata(message.id), self.agent_id
        )
        return {
            "self_echo": bool(
                self.agent_id
                and is_self_echo(
                    sender_id=message.sender_id or "",
                    sender_type=message.sender_type or "",
                    agent_id=self.agent_id,
                )
            ),
            "durably_processed": DeliveryStatus.PROCESSED in (status, cached),
            "durably_failed": status == DeliveryStatus.FAILED
            or (status is None and cached == DeliveryStatus.FAILED),
            "allow_failed_retry": not self.config.consume_all_queued,
        }

    def eligibility(
        self, message: MessageCreatedPayload | PlatformMessage
    ) -> DeliveryEligibility:
        eligibility = self.claims.delivery_eligibility(
            self.room_id, message.id, self.retry_tracker, **self._facts(message)
        )
        if eligibility == "completed":
            self.claims.remember_completed(self.room_id, message.id)
        return eligibility

    def awaits_retry_confirmation(self, message_id: str) -> bool:
        """A skipped settlement can still need its original after confirmation."""
        pending = self.claims.pending_settlement(self.room_id, message_id)
        return pending is not None and pending["continuation"]["kind"] == "retry"

    @contextlib.contextmanager
    def claim(self, events: tuple[MessageEvent, ...]) -> Iterator[bool]:
        acquired: list[str] = []
        try:
            for event in events:
                message = event.payload
                assert message is not None
                if (
                    self.claims.guarded_claim(
                        self.room_id,
                        message.id,
                        self.retry_tracker,
                        **self._facts(message),
                    )
                    != "runnable"
                ):
                    yield False
                    return
                acquired.append(message.id)
            yield True
        finally:
            for message_id in acquired:
                self.claims.release(self.room_id, message_id)

    async def processing(self, message_id: str) -> bool:
        with observe_delivery_phase("claim", self.room_id, message_id):
            accepted = await self.link.mark_processing(self.room_id, message_id)
        logger.debug(
            "Claim room=%s message=%s accepted=%s", self.room_id, message_id, accepted
        )
        return accepted

    async def confirm_processing(self, scope: TurnScope, revision: int) -> bool:
        if not self.host.owns_delivery(scope, revision):
            return False
        for event in scope.originals:
            assert event.payload is not None
            try:
                accepted = await self.processing(event.payload.id)
            except RoomExecutionStoppedError:
                accepted = False
            if not accepted or not self.host.owns_delivery(scope, revision):
                return False
        return True

    async def run(
        self,
        events: tuple[MessageEvent, ...],
        revision: int,
        *,
        control: bool = False,
    ) -> TurnOutcome:
        """Every source crosses the same gates before a handler can consume input."""
        while True:
            outcome = await self._run_once(events, revision, control=control)
            if outcome is not TurnOutcome.ADVANCED or not any(
                event.payload is not None
                and self.eligibility(event.payload) == "runnable"
                for event in events
            ):
                return outcome

    async def _run_once(
        self,
        events: tuple[MessageEvent, ...],
        revision: int,
        *,
        control: bool,
    ) -> TurnOutcome:
        members: list[MessageEvent] = []
        for event in events:
            assert event.payload is not None
            eligibility = self.eligibility(event.payload)
            if eligibility == "pending":
                if not await self.retry_settlement(event.payload.id):
                    return TurnOutcome.RETRY_LATER
                eligibility = self.eligibility(event.payload)
            if eligibility == "inflight":
                return TurnOutcome.RETRY_LATER
            if eligibility == "runnable":
                members.append(event)
        if not members:
            return TurnOutcome.ADVANCED
        originals = tuple(members)
        with self.claim(originals) as acquired:
            if not acquired:
                return TurnOutcome.RETRY_LATER
            return await self._run_claimed(originals, revision, control=control)

    async def _run_claimed(
        self, events: tuple[MessageEvent, ...], revision: int, *, control: bool
    ) -> TurnOutcome:
        scope = await self.host.begin_delivery()
        if scope is None:
            return self._unadmitted_outcome()
        charged: list[str] = []
        scope.originals = events
        try:
            if revision != self.host.control_revision:
                return self._unadmitted_outcome()
            if not control and not self.host.context_hydrated:
                await self.host.prepare_delivery_context()
            # Hydration can reveal durable completion after initial selection.
            scope.originals = tuple(
                event
                for event in events
                if event.payload is not None
                and self.eligibility(event.payload) not in {"completed", "failed"}
            )
            if not scope.originals:
                return TurnOutcome.ADVANCED
            if not control:
                scope.originals = self._charge_attempts(scope.originals, charged)
                if not scope.originals:
                    return TurnOutcome.ADVANCED
            if not await self.confirm_processing(scope, revision):
                return self._unadmitted_outcome()
            if control and not self.host.recognizes_delivery_control(
                scope.originals[0]
            ):
                return TurnOutcome.DECLINED
            scope.admit()
            await self.host.prepare_delivery_context()
            if revision != self.host.control_revision and scope.kind is None:
                return self._unadmitted_outcome()
            if not await self.host.execute_delivery(scope, control=control):
                return self.aborted_outcome(scope)
            if not scope.consumed:
                return TurnOutcome.DECLINED
            return (
                TurnOutcome.ADVANCED
                if await self.settle(scope)
                else TurnOutcome.RETRY_LATER
            )
        except InputNotAccepted as refusal:
            self.host.defer_delivery(self._first_id(scope), reason=refusal)
            return TurnOutcome.NOT_ACCEPTED
        except RoomExecutionStoppedError:
            return TurnOutcome.RETRY_LATER
        except Exception as error:  # noqa: BLE001 -- the delivery boundary owns failure settlement
            log_delivery_error(
                error, "Error processing messages in room %s", self.room_id
            )
            if not scope.admitted:
                return self._unadmitted_outcome()
            if control and scope.intent is None:
                for event in scope.originals:
                    assert event.payload is not None
                    self.retry_tracker.record_attempt(event.payload.id)
            self.store_intent(scope, DeliveryStatus.FAILED)
            final = not self._will_retry(scope)
            settled = await self.try_settle(scope)
            await self.host.report_delivery_failure(final=final)
            if not settled:
                return TurnOutcome.RETRY_LATER
            return TurnOutcome.ADVANCED
        finally:
            if scope.intent is None or scope.kind is ControlMode.INTERRUPT:
                self.discard_attempts(charged)
            self.host.finish_delivery(scope)

    @staticmethod
    def _first_id(scope: TurnScope) -> str | None:
        if scope.originals and scope.originals[0].payload is not None:
            return scope.originals[0].payload.id
        return None

    def _unadmitted_outcome(self) -> TurnOutcome:
        return TurnOutcome.PAUSED if self.host.is_stopped else TurnOutcome.RETRY_LATER

    @staticmethod
    def aborted_outcome(scope: TurnScope) -> TurnOutcome:
        if scope.kind is ControlMode.INTERRUPT:
            return TurnOutcome.ADVANCED
        return TurnOutcome.RETRY_LATER if scope.stop_observed else TurnOutcome.PAUSED

    def discard_attempts(self, attempts: Iterable[str]) -> None:
        for message_id in attempts:
            self.retry_tracker.discard_attempt(message_id)

    def _charge_attempts(
        self, events: tuple[MessageEvent, ...], charged: list[str]
    ) -> tuple[MessageEvent, ...]:
        runnable: list[MessageEvent] = []
        for event in events:
            assert event.payload is not None
            _, exceeded = self.retry_tracker.record_attempt(event.payload.id)
            if not exceeded:
                charged.append(event.payload.id)
                runnable.append(event)
        return tuple(runnable)

    def _will_retry(self, scope: TurnScope) -> bool:
        for event in scope.originals:
            assert event.payload is not None
            pending = self.claims.pending_settlement(self.room_id, event.payload.id)
            if pending is not None and pending["continuation"]["kind"] == "retry":
                return True
        return False

    def select_intent(
        self,
        scope: TurnScope,
        owner: TurnTask | None,
        *,
        timed_out: bool,
        control: bool = False,
    ) -> None:
        match scope.kind:
            case ControlMode.STOP:
                intent = None
            case ControlMode.INTERRUPT:
                intent = DeliveryStatus.PROCESSED
            case _ if scope.shutdown:
                intent = None
            case _ if timed_out:
                intent = DeliveryStatus.FAILED
            case _ if owner is None or not owner.task.done() or owner.task.cancelled():
                intent = None
            case _:
                assert owner is not None
                error = owner.task.exception()
                intent = (
                    DeliveryStatus.FAILED
                    if error is not None
                    else DeliveryStatus.PROCESSED
                )
                if isinstance(error, (InputNotAccepted, RoomExecutionStoppedError)):
                    intent = None
        if intent is DeliveryStatus.PROCESSED and not scope.consumed:
            intent = None
        if intent is DeliveryStatus.FAILED and control:
            for event in scope.originals:
                assert event.payload is not None
                self.retry_tracker.record_attempt(event.payload.id)
        self.store_intent(scope, intent)

    def store_intent(
        self,
        scope: TurnScope,
        intent: DeliveryStatus | None,
        *,
        error: str = GENERIC_PROVIDER_FAILURE_MESSAGE,
    ) -> None:
        if not scope.settle(intent):
            return
        for event in scope.originals:
            assert event.payload is not None
            match intent:
                case DeliveryStatus.PROCESSED:
                    self.claims.record_processed_settlement(
                        self.room_id, event.payload.id
                    )
                case DeliveryStatus.FAILED:
                    self.claims.record_failed_settlement(
                        self.room_id,
                        event.payload.id,
                        error,
                        self.retry_tracker,
                        not self.config.consume_all_queued,
                    )

    async def settle(self, scope: TurnScope) -> bool:
        results = [
            await self.write_settlement(event.payload.id)
            for event in scope.originals
            if event.payload is not None
            and self.claims.pending_settlement(self.room_id, event.payload.id)
            is not None
        ]
        return SettlementResult.UNCONFIRMED not in results

    async def try_settle(self, scope: TurnScope) -> bool:
        try:
            return await self.settle(scope)
        except RoomExecutionStoppedError:
            return False

    def is_parked(self, message_id: str) -> bool:
        pending = self.claims.pending_settlement(self.room_id, message_id)
        return pending is not None and pending["parked"] is not None

    async def write_settlement(self, message_id: str) -> SettlementResult:
        pending = self.claims.pending_settlement(self.room_id, message_id)
        if pending is None:
            raise ValueError(f"message {message_id} has no pending terminal write")
        with observe_delivery_phase("ack", self.room_id, message_id):
            match pending["status"]:
                case "processed":
                    result = await self.link.mark_processed_result(
                        self.room_id, message_id
                    )
                case "failed":
                    result = await self.link.mark_failed_result(
                        self.room_id, message_id, pending.get("error")
                    )
                case _:
                    raise ValueError(f"unknown terminal intent {pending['status']!r}")
        logger.debug(
            "Ack room=%s message=%s accepted=%s",
            self.room_id,
            message_id,
            result is SettlementResult.ACCEPTED,
        )
        match result:
            case SettlementResult.ACCEPTED:
                if not self.host.is_closing:
                    self.claims.confirm_settlement(
                        self.room_id, message_id, pending["token"], self.retry_tracker
                    )
                self.retry_not_before.pop(message_id, None)
            case SettlementResult.NOT_FOUND | SettlementResult.REJECTED:
                reason = (
                    ParkedReason.NotFound
                    if result is SettlementResult.NOT_FOUND
                    else ParkedReason.Rejected
                )
                self.claims.park_settlement(
                    self.room_id, message_id, pending["token"], reason
                )
                logger.warning(
                    "Terminal write parked room=%s message=%s result=%s",
                    self.room_id,
                    message_id,
                    result,
                )
            case SettlementResult.UNCONFIRMED:
                self.retry_not_before[message_id] = (
                    asyncio.get_running_loop().time() + self.config.idle_resync_seconds
                )
        return result

    async def retry_settlement(self, message_id: str) -> bool:
        pending = self.claims.pending_settlement(self.room_id, message_id)
        if pending is None or pending["parked"] is not None:
            return True
        if (
            self.host.is_stopped
            or asyncio.get_running_loop().time()
            < self.retry_not_before.get(message_id, 0.0)
        ):
            return False
        try:
            result = await self.write_settlement(message_id)
        except RoomExecutionStoppedError:
            return False
        if result is not SettlementResult.UNCONFIRMED:
            return True
        retries = self.claims.record_ack_retry(self.room_id, message_id)
        if retries == self.retry_tracker.max_retries:
            logger.warning(
                "Terminal write remains unconfirmed room=%s message=%s; retaining intent",
                self.room_id,
                message_id,
            )
        return False
