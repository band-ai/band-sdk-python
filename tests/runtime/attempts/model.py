"""An independent model of one input's execution and settlement obligations."""

from __future__ import annotations

from dataclasses import dataclass

from band_sdk_core import ClaimRegistry, ParkedReason, RetryTracker
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from band.client.streaming import DeliveryStatus
from tests.runtime.helpers import ROOM_ID

MESSAGE_ID = "original"
FAILURE = "the model failed"
MODEL_BUDGET = 2


@dataclass
class Obligation:
    status: DeliveryStatus
    attempts: int
    retries_execution: bool


class DeliveryModel(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.registry = ClaimRegistry()
        self.tracker = self.fresh_tracker()
        self.owned = False
        self.finished = False
        self.executions = 0
        self.owed: Obligation | None = None
        self.token: str | None = None
        self.old_tokens: list[str] = []

    @staticmethod
    def fresh_tracker() -> RetryTracker:
        return RetryTracker(max_retries=MODEL_BUDGET, max_tracked=1)

    @precondition(
        lambda self: not self.owned and not self.finished and self.owed is None
    )
    @rule()
    def execute(self) -> None:
        decision = self.registry.guarded_claim(ROOM_ID, MESSAGE_ID, self.tracker)
        assert decision == "runnable"
        self.owned = True
        self.executions += 1
        count, exhausted = self.tracker.record_attempt(MESSAGE_ID)
        assert (count, exhausted) == (self.executions, False)

    @precondition(lambda self: self.owned and self.owed is None)
    @rule()
    def fail(self) -> None:
        snapshot = self.registry.record_failed_settlement(
            ROOM_ID, MESSAGE_ID, FAILURE, self.tracker, True
        )
        self.owed = Obligation(
            DeliveryStatus.FAILED,
            self.executions,
            self.executions < MODEL_BUDGET,
        )
        self.token = snapshot["token"]

    @precondition(lambda self: self.owned and self.owed is None)
    @rule()
    def complete(self) -> None:
        snapshot = self.registry.record_processed_settlement(ROOM_ID, MESSAGE_ID)
        self.owed = Obligation(DeliveryStatus.PROCESSED, self.executions, False)
        self.token = snapshot["token"]

    @precondition(lambda self: self.owned and self.owed is not None)
    @rule()
    def release_execution(self) -> None:
        self.registry.release(ROOM_ID, MESSAGE_ID)
        self.owned = False

    @precondition(lambda self: not self.owned and self.owed is not None)
    @rule()
    def replace_context(self) -> None:
        self.tracker = self.fresh_tracker()

    @precondition(lambda self: self.owed is not None)
    @rule()
    def evict_ordinary_retry_history(self) -> None:
        self.tracker.record_attempt("another-input")

    @precondition(lambda self: self.owed is not None)
    @rule()
    def lose_response(self) -> None:
        assert (
            self.registry.guarded_claim(ROOM_ID, MESSAGE_ID, self.tracker) == "pending"
        )

    @precondition(lambda self: self.owed is not None)
    @rule()
    def park(self) -> None:
        assert self.token is not None
        assert self.registry.park_settlement(
            ROOM_ID, MESSAGE_ID, self.token, ParkedReason.NotFound
        )
        assert not self.registry.has_retryable_settlement(ROOM_ID)

    @rule()
    def reconnect(self) -> None:
        self.registry.unpark_acks(ROOM_ID)

    @precondition(lambda self: not self.owned and self.owed is not None)
    @rule()
    def confirm(self) -> None:
        assert self.token is not None and self.owed is not None
        assert self.registry.confirm_settlement(
            ROOM_ID, MESSAGE_ID, self.token, self.tracker
        )
        self.finished = not self.owed.retries_execution
        self.owed = None
        self.old_tokens.append(self.token)
        self.token = None

    @precondition(lambda self: bool(self.old_tokens))
    @rule()
    def repeat_old_confirmation(self) -> None:
        assert not self.registry.confirm_settlement(
            ROOM_ID, MESSAGE_ID, self.old_tokens[-1], self.tracker
        )

    @invariant()
    def retained_obligations_match_the_first_execution_outcome(self) -> None:
        pending = self.registry.pending_settlement(ROOM_ID, MESSAGE_ID)
        if self.owed is None:
            assert pending is None
            return
        assert pending is not None
        assert (pending["token"], pending["status"]) == (self.token, self.owed.status)
        if self.owed.status is DeliveryStatus.FAILED:
            assert pending["error"] == FAILURE
        continuation = pending["continuation"]
        if self.owed.retries_execution:
            assert continuation == {
                "kind": "retry",
                "attempts": self.owed.attempts,
                "max_retries": MODEL_BUDGET,
            }
        else:
            assert continuation == {"kind": "finished"}

    @invariant()
    def finished_inputs_never_become_runnable(self) -> None:
        if self.finished:
            assert (
                self.registry.delivery_eligibility(ROOM_ID, MESSAGE_ID, self.tracker)
                == "completed"
            )
