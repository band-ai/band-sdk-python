from __future__ import annotations

import pytest

from tests.contracts.runtime.modelwedge import (
    AlwaysOffSchema,
    ModelWedgeFixture,
    OffSchemaOnce,
    UncertainOnce,
)


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestModelWedge(ModelWedgeFixture):
    def test_an_off_schema_reply_counts_as_an_attempt_and_the_next_attempt_answers(
        self,
    ) -> None:
        agent = self.start_provider(OffSchemaOnce())
        result = agent.receive(
            self.question("Explain this ambiguous English instruction")
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["producer"], "llm")
        self.assertIsNone(self.store.get("execution"))
        self.assertEqual(self.store.get("queue"), [])
        self.assertEqual(self.provider.calls, ["respond", "respond"])
        self.assertEqual(len(self.finished("invalid_model_reply")), 1)
        rejected = [
            r["data"] for r in self.store.records() if r["kind"] == "llm_rejected"
        ]
        self.assertEqual(len(rejected), 1)
        self.assertIn("Invalid LLM takeover scope", rejected[0]["reason"])

    def test_repeated_off_schema_replies_exhaust_the_attempt_budget_without_pinning(
        self,
    ) -> None:
        agent = self.start_provider(AlwaysOffSchema())
        event = self.question("Explain this ambiguous English instruction")
        result = agent.receive(event)
        self.assertEqual(result["status"], "held")
        self.assertIn("attempt budget exhausted", result["reason"])
        self.assertIsNone(self.store.get("execution"))
        self.assertEqual(self.store.get("attempts:" + event["id"]), 3)
        self.assertEqual(self.provider.calls.count("respond"), 3)
        self.assertEqual(len(self.finished("invalid_model_reply")), 3)

    def test_an_authorized_retry_abandons_a_completed_but_uncertain_provider_record(
        self,
    ) -> None:
        agent = self.start_provider(UncertainOnce())
        result = agent.receive(
            self.question("Explain this ambiguous English instruction")
        )
        # No definitive result: never resample silently; hold with the execution kept.
        self.assertEqual(result["status"], "held")
        pinned = self.store.get("execution")
        self.assertIsNotNone(pinned)
        retried = agent.control(
            "operator", {"id": "unstick", "action": "retry", "reason": "Provider 503"}
        )
        self.assertEqual(retried["status"], "completed")
        abandoned = [
            r["data"]
            for r in self.store.records()
            if r["kind"] == "execution_abandoned"
        ]
        self.assertEqual([a["id"] for a in abandoned], [pinned["id"]])
        self.assertIsNone(self.store.get("execution"))
        self.assertEqual(self.provider.calls.count("respond"), 2)

    def test_a_retry_does_not_abandon_a_definitive_provider_record(self) -> None:
        agent = self.start_provider(OffSchemaOnce())
        agent.receive(self.question("Explain this ambiguous English instruction"))
        retried = agent.control(
            "operator", {"id": "noop", "action": "retry", "reason": "Nothing pending"}
        )
        self.assertNotEqual(retried["status"], "held")
        self.assertEqual(
            [r for r in self.store.records() if r["kind"] == "execution_abandoned"], []
        )
