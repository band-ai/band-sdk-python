"""Admission and durable settlement shared by every real message attempt."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterable, Iterator
from dataclasses import replace
from enum import Enum
from time import monotonic
from typing import Protocol, TypedDict

from band_sdk_core import (
    ClaimRegistry,
    DeliveryEligibility,
    ParkedReason,
    RetryTracker,
    is_self_echo,
)

from band.client.streaming import ControlMode, DeliveryStatus
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
from band.runtime.originals import message_id, payload
from band.runtime.selection import Candidate
from band.runtime.sessionconfig import SessionConfig

logger = logging.getLogger(__name__)

# Why a refused terminal write stays parked, by the platform's answer.
PARKED_REASONS = {
    SettlementResult.NOT_FOUND: ParkedReason.NotFound,
    SettlementResult.REJECTED: ParkedReason.Rejected,
}


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
    # A readable room after a 404 lets the SDK infer that the original is gone.
    ABSENT = "absent"


class DeliveryFacts(TypedDict):
    """What the room knows about a delivery, for the core eligibility rule."""

    self_echo: bool
    durably_processed: bool
    durably_failed: bool


class DeliveryHost(Protocol):
    """Room effects whose ownership outlives an individual delivery operation."""

    @property
    def control_revision(self) -> int: ...

    @property
    def is_stopped(self) -> bool: ...

    @property
    def context_hydrated(self) -> bool: ...

    @property
    def delivery_context_token(self) -> object: ...

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
        # Loop time before which an unconfirmed terminal write is not retried, so
        # arrivals cannot multiply an outage's writes. A due or requested
        # reconciliation clears it.
        self.retry_not_before: dict[str, float] = {}

    def lift_write_backoff(self) -> None:
        """Let every unconfirmed terminal write retry now, for a reconciliation."""
        self.retry_not_before.clear()

    def _facts(self, candidate: Candidate) -> DeliveryFacts:
        observation = candidate.observed_metadata
        message = payload(candidate.event)
        status = delivery_status_for_agent(
            observation if observation is not None else message.metadata,
            self.agent_id,
        )
        cached = (
            delivery_status_for_agent(
                self.host.delivery_metadata(message.id), self.agent_id
            )
            if observation is None
            else None
        )
        return DeliveryFacts(
            # Backend nulls would raise TypeError from is_self_echo's str params.
            self_echo=bool(
                self.agent_id
                and is_self_echo(
                    sender_id=message.sender_id or "",
                    sender_type=message.sender_type or "",
                    agent_id=self.agent_id,
                )
            ),
            durably_processed=DeliveryStatus.PROCESSED in (status, cached),
            durably_failed=status == DeliveryStatus.FAILED
            or (status is None and cached == DeliveryStatus.FAILED),
        )

    def eligibility(
        self, candidate: Candidate, *, remember_completion: bool = True
    ) -> DeliveryEligibility:
        eligibility = self.claims.delivery_eligibility(
            self.room_id,
            candidate.message_id,
            self.retry_tracker,
            **self._facts(candidate),
        )
        if eligibility == "completed" and remember_completion:
            self.claims.remember_completed(self.room_id, candidate.message_id)
        return eligibility

    def owes_write(self, identity: str) -> bool:
        """Whether a terminal write the platform has neither accepted nor refused remains."""
        pending = self.claims.pending_settlement(self.room_id, identity)
        return pending is not None and pending["parked"] is None

    def keeps_original(self, identity: str) -> bool:
        """Whether the original must outlive its write, for the retry it permits."""
        pending = self.claims.pending_settlement(self.room_id, identity)
        return pending is not None and pending["continuation"]["kind"] == "retry"

    async def is_gone(self, identity: str) -> bool:
        """Infer a missing message from a 404 followed by a readable room.

        The probe is not atomic with the write: restored access between requests
        can retire an original that still exists.
        """
        try:
            await self.link.get_next_message(self.room_id)
        except Exception:  # noqa: BLE001 -- a failed read leaves the 404 ambiguous
            logger.debug(
                "Room %s unreadable after a 404 on message %s; keeping it",
                self.room_id,
                identity,
            )
            return False
        logger.info(
            "Room %s readable after a 404 on message %s; inferring deletion",
            self.room_id,
            identity,
        )
        return True

    def retire_gone(self, identity: str) -> None:
        """Drop the pending settlement of an original inferred missing."""
        # Local completion releases the pending settlement without a platform write.
        self.claims.remember_completed(self.room_id, identity)
        self.retry_not_before.pop(identity, None)

    @contextlib.contextmanager
    def claim(self, members: tuple[Candidate, ...]) -> Iterator[bool]:
        acquired: list[str] = []
        try:
            for candidate in members:
                if (
                    self.claims.guarded_claim(
                        self.room_id,
                        candidate.message_id,
                        self.retry_tracker,
                        **self._facts(candidate),
                    )
                    != "runnable"
                ):
                    yield False
                    return
                acquired.append(candidate.message_id)
            yield True
        finally:
            for held in acquired:
                self.claims.release(self.room_id, held)

    async def processing(self, identity: str) -> SettlementResult:
        with observe_delivery_phase("claim", self.room_id, identity):
            result = await self.link.mark_processing_result(self.room_id, identity)
        logger.debug(
            "Claim room=%s message=%s result=%s", self.room_id, identity, result
        )
        return result

    async def confirm_processing(self, scope: TurnScope, revision: int) -> bool:
        """Open every original's attempt; one the platform no longer has leaves the scope."""
        if not self.host.owns_delivery(scope, revision):
            return False
        present: list[MessageEvent] = []
        for event in scope.originals:
            identity = message_id(event)
            try:
                result = await self.processing(identity)
            except RoomExecutionStoppedError:
                return False
            if result is SettlementResult.NOT_FOUND and await self.is_gone(identity):
                continue
            if result is not SettlementResult.ACCEPTED or not self.host.owns_delivery(
                scope, revision
            ):
                return False
            present.append(event)
        scope.originals = tuple(present)
        return True

    async def run(
        self,
        members: tuple[Candidate, ...],
        revision: int,
        *,
        control: bool = False,
    ) -> TurnOutcome:
        """Every source crosses the same gates before a handler can consume input."""
        current = list(members)
        while True:
            outcome = await self._run_once(current, revision, control=control)
            if outcome is not TurnOutcome.ADVANCED or not any(
                self.eligibility(candidate) == "runnable" for candidate in current
            ):
                return outcome

    async def _run_once(
        self,
        members: list[Candidate],
        revision: int,
        *,
        control: bool,
    ) -> TurnOutcome:
        runnable: list[Candidate] = []
        for candidate in members:
            eligibility = self.eligibility(candidate)
            if eligibility == "pending":
                if not await self.retry_settlement(candidate.message_id):
                    return TurnOutcome.RETRY_LATER
                eligibility = self.eligibility(candidate)
            if eligibility == "inflight":
                return TurnOutcome.RETRY_LATER
            if eligibility == "runnable":
                runnable.append(candidate)
        if not runnable:
            return TurnOutcome.ADVANCED
        members[:] = runnable
        with self.claim(tuple(members)) as acquired:
            if not acquired:
                return TurnOutcome.RETRY_LATER
            return await self._run_claimed(members, revision, control=control)

    async def _run_claimed(
        self, members: list[Candidate], revision: int, *, control: bool
    ) -> TurnOutcome:
        scope = await self.host.begin_delivery()
        if scope is None:
            return self._unadmitted_outcome()
        charged: list[str] = []
        scope.originals = tuple(candidate.event for candidate in members)
        try:
            # The first phase that returns an outcome ends the attempt.
            return (
                await self._prepare(scope, members, revision, charged, control=control)
                or await self._admit(scope, revision, control=control)
                or await self._execute(scope, control=control)
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
            return await self._fail(scope, control=control)
        finally:
            if scope.intent is None or scope.kind is ControlMode.INTERRUPT:
                self.discard_attempts(charged)
            self.host.finish_delivery(scope)

    async def _prepare(
        self,
        scope: TurnScope,
        members: list[Candidate],
        revision: int,
        charged: list[str],
        *,
        control: bool,
    ) -> TurnOutcome | None:
        """Fix the originals still owed a turn and charge them, or end the attempt."""
        if revision != self.host.control_revision:
            return self._unadmitted_outcome()
        context_token = self.host.delivery_context_token
        if not control and not self.host.context_hydrated:
            await self.host.prepare_delivery_context()
        if self.host.delivery_context_token is not context_token:
            members[:] = [self._hydrated(candidate) for candidate in members]
        # Hydration can reveal durable completion after initial selection.
        scope.originals = tuple(
            candidate.event
            for candidate in members
            if self.eligibility(candidate) not in {"completed", "failed"}
        )
        if not control:
            scope.originals = self._charge_attempts(scope.originals, charged)
        return None if scope.originals else TurnOutcome.ADVANCED

    async def _admit(
        self, scope: TurnScope, revision: int, *, control: bool
    ) -> TurnOutcome | None:
        """Admit once every original is confirmed processing, or end the attempt."""
        if not await self.confirm_processing(scope, revision):
            return self._unadmitted_outcome()
        if not scope.originals:
            return TurnOutcome.ABSENT
        if control and not self.host.recognizes_delivery_control(scope.originals[0]):
            return TurnOutcome.DECLINED
        scope.admit()
        await self.host.prepare_delivery_context()
        if revision != self.host.control_revision and scope.kind is None:
            return self._unadmitted_outcome()
        return None

    async def _execute(self, scope: TurnScope, *, control: bool) -> TurnOutcome:
        executed = await self.host.execute_delivery(scope, control=control)
        if executed and not scope.consumed:
            return TurnOutcome.DECLINED
        await self.settle(scope)
        return self.settled_outcome(scope)

    async def _fail(self, scope: TurnScope, *, control: bool) -> TurnOutcome:
        """Settle an admitted attempt whose execution raised as one failure."""
        if control and scope.intent is None:
            self._charge_control_failure(scope)
        self.store_intent(scope, DeliveryStatus.FAILED)
        final = not self._will_retry(scope)
        await self.settle(scope)
        await self.host.report_delivery_failure(final=final)
        return self.settled_outcome(scope)

    def _hydrated(self, candidate: Candidate) -> Candidate:
        metadata = self.host.delivery_metadata(candidate.message_id)
        if metadata is None:
            return candidate
        return replace(candidate, observed_metadata=metadata)

    @staticmethod
    def _first_id(scope: TurnScope) -> str | None:
        return message_id(scope.originals[0]) if scope.originals else None

    def _unadmitted_outcome(self) -> TurnOutcome:
        return TurnOutcome.PAUSED if self.host.is_stopped else TurnOutcome.RETRY_LATER

    def settled_outcome(self, scope: TurnScope) -> TurnOutcome:
        """What an admitted attempt leaves behind, from its intent and its writes alone."""
        if scope.intent is None:
            return (
                TurnOutcome.RETRY_LATER if scope.stop_observed else TurnOutcome.PAUSED
            )
        if any(self.owes_write(message_id(event)) for event in scope.originals):
            return TurnOutcome.RETRY_LATER
        return TurnOutcome.ADVANCED

    def discard_attempts(self, attempts: Iterable[str]) -> None:
        for attempt in attempts:
            self.retry_tracker.discard_attempt(attempt)

    def _charge_attempts(
        self, events: tuple[MessageEvent, ...], charged: list[str]
    ) -> tuple[MessageEvent, ...]:
        runnable: list[MessageEvent] = []
        for event in events:
            if self._record_attempt(message_id(event)):
                charged.append(message_id(event))
                runnable.append(event)
        return tuple(runnable)

    def _record_attempt(self, identity: str) -> bool:
        """Charge one attempt; whether the retry budget still allows it."""
        attempts, exceeded = self.retry_tracker.record_attempt(identity)
        if exceeded:
            logger.warning(
                "Room %s: Message %s exceeded max retries (%s attempts)",
                self.room_id,
                identity,
                attempts,
            )
        return not exceeded

    def _charge_control_failure(self, scope: TurnScope) -> None:
        for event in scope.originals:
            self._record_attempt(message_id(event))

    def _will_retry(self, scope: TurnScope) -> bool:
        return any(self.keeps_original(message_id(event)) for event in scope.originals)

    def select_intent(
        self,
        scope: TurnScope,
        owner: TurnTask | None,
        *,
        timed_out: bool,
        control: bool = False,
    ) -> None:
        intent = self._terminal_intent(scope, owner, timed_out=timed_out)
        if intent is DeliveryStatus.PROCESSED and not scope.consumed:
            intent = None
        if intent is DeliveryStatus.FAILED and control:
            self._charge_control_failure(scope)
        self.store_intent(scope, intent)

    @staticmethod
    def _terminal_intent(
        scope: TurnScope, owner: TurnTask | None, *, timed_out: bool
    ) -> DeliveryStatus | None:
        """Precedence: STOP, then INTERRUPT, shutdown, timeout, and last the handler's result."""
        if scope.kind is ControlMode.STOP:
            return None
        if scope.kind is ControlMode.INTERRUPT:
            return DeliveryStatus.PROCESSED
        if scope.shutdown:
            return None
        if timed_out:
            return DeliveryStatus.FAILED
        if owner is None or not owner.task.done() or owner.task.cancelled():
            return None
        error = owner.task.exception()
        if isinstance(error, (InputNotAccepted, RoomExecutionStoppedError)):
            return None
        return DeliveryStatus.FAILED if error is not None else DeliveryStatus.PROCESSED

    def store_intent(
        self,
        scope: TurnScope,
        intent: DeliveryStatus | None,
    ) -> None:
        if not scope.settle(intent):
            return
        for event in scope.originals:
            match intent:
                case DeliveryStatus.PROCESSED:
                    self.claims.record_processed_settlement(
                        self.room_id, message_id(event)
                    )
                case DeliveryStatus.FAILED:
                    self.claims.record_failed_settlement(
                        self.room_id,
                        message_id(event),
                        GENERIC_PROVIDER_FAILURE_MESSAGE,
                        self.retry_tracker,
                        True,
                    )

    async def settle(self, scope: TurnScope) -> None:
        """Send each original's stored terminal write; a stopped room leaves it owed."""
        with contextlib.suppress(RoomExecutionStoppedError):
            for event in scope.originals:
                identity = message_id(event)
                if self.claims.pending_settlement(self.room_id, identity) is not None:
                    await self.write_settlement(identity)

    async def write_settlement(self, identity: str) -> SettlementResult:
        pending = self.claims.pending_settlement(self.room_id, identity)
        if pending is None:
            raise ValueError(f"message {identity} has no pending terminal write")
        with observe_delivery_phase("ack", self.room_id, identity):
            match pending["status"]:
                case DeliveryStatus.PROCESSED:
                    result = await self.link.mark_processed_result(
                        self.room_id, identity
                    )
                case DeliveryStatus.FAILED:
                    result = await self.link.mark_failed_result(
                        self.room_id, identity, pending.get("error")
                    )
                case _:
                    raise ValueError(f"unknown terminal intent {pending['status']!r}")
        logger.debug(
            "Ack room=%s message=%s accepted=%s",
            self.room_id,
            identity,
            result is SettlementResult.ACCEPTED,
        )
        match result:
            case SettlementResult.ACCEPTED:
                self.claims.confirm_settlement(
                    self.room_id, identity, pending["token"], self.retry_tracker
                )
                self.retry_not_before.pop(identity, None)
            case SettlementResult.NOT_FOUND if await self.is_gone(identity):
                self.retire_gone(identity)
            case SettlementResult.NOT_FOUND | SettlementResult.REJECTED:
                self.claims.park_settlement(
                    self.room_id, identity, pending["token"], PARKED_REASONS[result]
                )
                logger.warning(
                    "Terminal write parked room=%s message=%s result=%s",
                    self.room_id,
                    identity,
                    result,
                )
            case SettlementResult.UNCONFIRMED:
                self.retry_not_before[identity] = (
                    asyncio.get_running_loop().time() + self.config.idle_resync_seconds
                )
        return result

    async def retry_settlement(self, identity: str) -> bool:
        if not self.owes_write(identity):
            return True
        if (
            self.host.is_stopped
            or asyncio.get_running_loop().time()
            < self.retry_not_before.get(identity, 0.0)
        ):
            return False
        try:
            result = await self.write_settlement(identity)
        except RoomExecutionStoppedError:
            return False
        if result is not SettlementResult.UNCONFIRMED:
            return True
        retries = self.claims.record_ack_retry(self.room_id, identity)
        if retries == self.retry_tracker.max_retries:
            logger.warning(
                "Terminal write remains unconfirmed room=%s message=%s; retaining intent",
                self.room_id,
                identity,
            )
        return False
