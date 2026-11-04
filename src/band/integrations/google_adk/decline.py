"""Stop qualified ADK runs after complete successful decline batches."""

from __future__ import annotations

from collections import Counter

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from band.runtime.tools.types import TurnEffect


class DeclineBatch:
    """Track one finalized response for one SDK-created non-streaming runner."""

    def __init__(self) -> None:
        self._invocation_id: str | None = None
        self._expected: Counter[str] = Counter()
        self._completed: Counter[str] = Counter()
        self._succeeded = True
        self._declined = False

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
        self, invocation_id: str, name: str, succeeded: bool, effect: TurnEffect
    ) -> None:
        """Record the actual bridge outcome without trusting generated call IDs."""
        if invocation_id != self._invocation_id:
            return
        self._completed[name] += 1
        self._succeeded = self._succeeded and succeeded
        self._declined = self._declined or (succeeded and effect.ends_turn)

    def before_model(
        self, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        """Consume the batch once and bypass only a fully observed success."""
        stop = (
            callback_context.invocation_id == self._invocation_id
            and bool(self._expected)
            and self._completed == self._expected
            and self._succeeded
            and self._declined
        )
        self._reset()
        return LlmResponse() if stop else None

    def _reset(self) -> None:
        self._invocation_id = None
        self._expected.clear()
        self._completed.clear()
        self._succeeded = True
        self._declined = False
