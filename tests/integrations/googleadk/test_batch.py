"""Only a matching finalized and fully successful ADK roster may stop."""

from __future__ import annotations

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from hypothesis import given
from hypothesis import strategies as st

from band.integrations.google_adk.decline import DeclineBatch
from band.runtime.tools.types import TurnEffect
from tests.integrations.googleadk.callbacks import context, response


class TestBatch:
    def test_late_result_from_same_invocation_cannot_complete_a_new_round(self) -> None:
        batch = DeclineBatch()
        batch.after_model(context(), response("decline"))
        previous = batch.capture()
        batch.after_model(context(), response("decline"))
        batch.completed(
            "current", "decline", True, TurnEffect.DECLINE, round_outcome=previous
        )
        assert batch.before_model(context(), LlmRequest()) is None

    @pytest.mark.parametrize(
        "observed",
        [[], ["decline"], ["decline", "decline", "decline"], ["decline", "other"]],
    )
    def test_incomplete_extra_and_wrong_name_results_cannot_stop(
        self, observed: list[str]
    ) -> None:
        batch = DeclineBatch()
        batch.after_model(context(), response("decline", "decline"))
        for name in observed:
            batch.completed("current", name, True, TurnEffect.DECLINE)

        assert batch.before_model(context(), LlmRequest()) is None

    def test_a_complete_batch_is_consumed_only_once(self) -> None:
        batch = DeclineBatch()
        batch.after_model(context(), response("decline"))
        batch.completed("current", "decline", True, TurnEffect.DECLINE)

        assert batch.before_model(context(), LlmRequest()) == LlmResponse()
        assert batch.before_model(context(), LlmRequest()) is None

    def test_partial_response_cannot_reuse_an_old_batch(self) -> None:
        batch = DeclineBatch()
        batch.after_model(context(), response("decline"))
        batch.completed("current", "decline", True, TurnEffect.DECLINE)
        batch.after_model(context(), response("decline", partial=True))
        batch.completed("current", "decline", True, TurnEffect.DECLINE)

        assert batch.before_model(context(), LlmRequest()) is None

    def test_non_tool_response_replaces_the_previous_roster(self) -> None:
        batch = DeclineBatch()
        batch.after_model(context(), response("decline"))
        batch.completed("current", "decline", True, TurnEffect.DECLINE)
        batch.after_model(context(), LlmResponse())

        assert batch.before_model(context(), LlmRequest()) is None

    def test_foreign_completion_does_not_satisfy_current_invocation(self) -> None:
        batch = DeclineBatch()
        batch.after_model(context(), response("decline"))
        batch.completed("old", "decline", True, TurnEffect.DECLINE)

        assert batch.before_model(context(), LlmRequest()) is None

    def test_foreign_pre_model_boundary_consumes_without_stopping(self) -> None:
        batch = DeclineBatch()
        batch.after_model(context(), response("decline"))
        batch.completed("current", "decline", True, TurnEffect.DECLINE)

        assert batch.before_model(context("other"), LlmRequest()) is None
        assert batch.before_model(context(), LlmRequest()) is None

    def test_late_completion_cannot_stop_after_an_incomplete_boundary(self) -> None:
        batch = DeclineBatch()
        batch.after_model(context(), response("decline"))
        assert batch.before_model(context(), LlmRequest()) is None
        batch.completed("current", "decline", True, TurnEffect.DECLINE)

        assert batch.before_model(context(), LlmRequest()) is None

    @given(
        rounds=st.lists(
            st.tuples(
                st.integers(min_value=1, max_value=4),
                st.lists(st.booleans(), max_size=5),
            ),
            min_size=1,
            max_size=12,
        )
    )
    def test_generated_round_sequences_do_not_leak_completion_or_failure_state(
        self, rounds: list[tuple[int, list[bool]]]
    ) -> None:
        batch = DeclineBatch()
        for expected, outcomes in rounds:
            batch.after_model(context(), response(*(["decline"] * expected)))
            for succeeded in outcomes:
                batch.completed("current", "decline", succeeded, TurnEffect.DECLINE)
            complete_success = len(outcomes) == expected and all(outcomes)
            actual = batch.before_model(context(), LlmRequest())
            assert (actual is not None) == complete_success
            assert batch.before_model(context(), LlmRequest()) is None
