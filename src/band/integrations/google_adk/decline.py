"""Stop qualified ADK runs after complete successful decline batches."""

from __future__ import annotations

from collections import Counter

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from band.core.turn import RoundOutcome
from band.runtime.tools.types import TurnEffect


class DeclineBatch:
    """Track one finalized response for one SDK-created non-streaming runner."""

    def __init__(self) -> None:
        self._invocation_id: str | None = None
        self._expected: Counter[str] = Counter()
        self._completed: Counter[str] = Counter()
        self._outcome = RoundOutcome()

    def after_model(
        self, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> None:
        """Replace the roster before native ADK dispatch starts."""
        self._reset()
        if llm_response.partial:
            return
        self._invocation_id = callback_context.invocation_id
        if llm_response.content and llm_response.content.parts:
            self._expected.update(
                part.function_call.name
                for part in llm_response.content.parts
                if part.function_call and part.function_call.name
            )

    def completed(
        self,
        invocation_id: str,
        name: str,
        succeeded: bool,
        effect: TurnEffect,
        *,
        round_outcome: RoundOutcome | None = None,
    ) -> None:
        """Record the actual bridge outcome without trusting generated call IDs."""
        if invocation_id != self._invocation_id or (
            round_outcome is not None and round_outcome is not self._outcome
        ):
            return
        self._completed[name] += 1
        self._outcome.record(effect, succeeded)

    def capture(self) -> RoundOutcome:
        """Bind an executing call to the round that dispatched it."""
        return self._outcome

    def before_model(
        self, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        """Consume the batch once and bypass only a fully observed success."""
        stop = self._outcome.should_stop(
            complete=(
                callback_context.invocation_id == self._invocation_id
                and bool(self._expected)
                and self._completed == self._expected
            )
        )
        self._reset()
        return LlmResponse() if stop else None

    def _reset(self) -> None:
        self._invocation_id = None
        self._expected.clear()
        self._completed.clear()
        self._outcome = RoundOutcome()
