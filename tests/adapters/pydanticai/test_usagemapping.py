"""UsageMapping behavior for Pydantic AI."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pydantic_ai.messages import ModelRequest, UserPromptPart

from band.adapters.pydantic_ai import PydanticAIAdapter
from band.core.types import TurnUsage
from tests.adapters.pydanticai.kit import make_usage_response


class TestUsageMapping:
    """Tests for the Emit.USAGE seam's usage mapping."""

    def test_usage_from_result_reads_the_usage_property(self):
        """Maps the run result's ``usage`` (a property since 2.x) onto TurnUsage.

        Reading it as a method instead would raise, and the guarded read would then
        report zeros for every turn — silent, so this is the guard.
        """

        result = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                cache_read_tokens=5,
                cache_write_tokens=0,
            )
        )
        assert PydanticAIAdapter._usage_from_result(result) == TurnUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=0,
        )

    def test_usage_from_result_swallows_errors(self):
        """Usage that fails to read yields empty usage, never propagates."""

        class Unreadable:
            @property
            def usage(self) -> Any:
                raise RuntimeError("no usage")

        assert PydanticAIAdapter._usage_from_result(Unreadable()) == TurnUsage()

    def test_usage_from_messages_sums_model_responses(self):
        """The benign-path fallback sums usage across captured ModelResponses.

        Covers the empty-final-response path (no AgentRunResultEvent fires) where
        the turn still spent tokens — each ModelResponse carries its own usage.
        """

        messages = [
            ModelRequest(parts=[]),  # non-response: ignored
            make_usage_response(100, 20),
            make_usage_response(130, 8),
        ]
        assert PydanticAIAdapter._usage_from_messages(messages) == TurnUsage(
            input_tokens=230, output_tokens=28
        )

    def test_usage_from_messages_empty_when_no_responses(self):
        """No ModelResponse in the captured messages → empty usage."""

        assert (
            PydanticAIAdapter._usage_from_messages([ModelRequest(parts=[])])
            == TurnUsage()
        )

    def test_new_run_messages_isolates_this_run_despite_history_merge(self):
        """Identity (not position) isolates this run when pydantic-ai merges history.

        Regression guard: pydantic-ai's ``_clean_message_history`` merges adjacent
        same-type messages in the passed history (e.g. the injected participants +
        contacts requests), so ``captured`` is *shorter* than the raw prior history
        and a ``len(prior)`` slice would drop this turn's response. Real API
        responses keep their identity, so the identity filter still isolates this
        run — and combined with the ModelResponse-only sum, yields only this turn's
        usage.
        """

        # Prior history: a real response, then two instruction-less requests that
        # pydantic-ai would merge into one on the next run.
        prior_resp = make_usage_response(100, 20)
        req_participants = ModelRequest(parts=[UserPromptPart(content="[System]: p")])
        req_contacts = ModelRequest(parts=[UserPromptPart(content="[System]: c")])
        prior = [prior_resp, req_participants, req_contacts]
        prior_ids = {id(m) for m in prior}

        # captured after cleaning: prior_resp survives by identity, the two
        # requests are merged into ONE new object, then this run's new response is
        # appended. So len(captured)=3 < len(prior)=3+... a positional
        # captured[len(prior):] would slice to empty and drop new_resp.
        merged_req = ModelRequest(parts=[UserPromptPart(content="[System]: p\nc")])
        new_resp = make_usage_response(130, 8)
        captured = [prior_resp, merged_req, new_resp]

        this_run = PydanticAIAdapter._new_run_messages(captured, prior_ids)
        # The merged request (new identity) rides along but carries no usage; only
        # this run's response contributes.
        assert PydanticAIAdapter._usage_from_messages(this_run) == TurnUsage(
            input_tokens=130, output_tokens=8
        )
