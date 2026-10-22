from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from remote_agent.store import Store
from tests.contracts.runtime import hybrid
from tests.contracts.runtime.modelhistoryrejection import (
    QUERY,
    HistoryRejectionFixture,
    InterruptedHost,
)


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestHistoryRejection(HistoryRejectionFixture):
    def test_disallowed_history_kind_is_a_definitive_rejection(self) -> None:
        self.assert_recovers([dict(QUERY, kind="model_output")])

    def test_history_page_limit_is_a_definitive_rejection(self) -> None:
        self.assert_recovers([dict(QUERY, limit=21)])

    def test_history_cursor_beyond_the_real_head_is_a_definitive_rejection(
        self,
    ) -> None:
        self.assert_recovers([dict(QUERY, through=9_007_199_254_740_991)])

    def test_history_cursor_order_is_a_definitive_rejection(self) -> None:
        self.assert_recovers([dict(QUERY, after=2, through=1)])

    def test_history_query_text_byte_limit_is_a_definitive_rejection(self) -> None:
        self.assert_recovers([dict(QUERY, query="é" * 129)])

    def test_a_real_page_over_the_context_limit_is_a_definitive_rejection(self) -> None:
        for _ in range(20):
            self.store.commit("fault", {"note": "x" * 7900})
        self.assert_recovers([dict(QUERY, kind="fault", limit=20)])

    def test_third_history_query_is_rejected_after_preserving_completed_pages(
        self,
    ) -> None:
        self.assert_recovers([QUERY, QUERY, QUERY], calls=4)
        pages = [r for r in self.store.records() if r["kind"] == "history_query"]
        self.assertEqual(len(pages), 2)
        self.assertEqual(len(self.provider.bundles[2]["history_pages"]), 2)

    def test_repeated_bad_queries_exhaust_attempts_without_pinning_or_extra_calls(
        self,
    ) -> None:
        agent = self.start_queries([dict(QUERY, limit=21)], repeat=True, max_attempts=2)
        event = self.question("Needs older evidence and model judgment")
        result = agent.receive(event)
        self.assertEqual(result["status"], "held")
        self.assertIn("attempt budget exhausted", result["reason"])
        self.assertIsNone(self.store.get("execution"))
        self.assertEqual(self.store.get("attempts:" + event["id"]), 2)
        self.assertEqual(self.store.get("model_calls"), 2)
        self.assertEqual(
            len([r for r in self.store.records() if r["kind"] == "llm_rejected"]), 2
        )
        self.assertEqual(agent.recover(), result)
        retried = agent.control(
            "operator",
            {
                "id": "retry-exhausted",
                "action": "retry",
                "reason": "Recheck the bounded exhausted input",
            },
        )
        self.assertEqual(retried["status"], "held")
        self.assertEqual(self.store.get("model_calls"), 2)
        self.assertEqual(self.provider.calls.count("respond"), 2)

    def test_default_attempts_share_one_input_history_budget_and_keep_its_pages(
        self,
    ) -> None:
        agent = self.start_queries([QUERY], repeat=True)
        event = self.question("Needs older evidence and model judgment")
        result = agent.receive(event)
        pages = [row for row in self.store.records() if row["kind"] == "history_query"]
        self.assertEqual(
            len(pages),
            2,
            "Response attempts must not reset the two-query input ceiling",
        )
        self.assertEqual(
            result, {"status": "held", "reason": "Response attempt budget exhausted"}
        )
        self.assertEqual(self.store.get("attempts:" + event["id"]), 3)
        self.assertEqual(self.store.get("model_calls"), 5)
        self.assertEqual(
            [len(bundle["history_pages"]) for bundle in self.provider.bundles],
            [0, 1, 2, 2, 2],
        )
        self.assertIsNone(self.store.get("execution"))
        self.assertEqual(self.store.get("state"), {"count": 0})
        self.assertEqual(agent.recover(), result)
        self.assertEqual(self.store.get("model_calls"), 5)

    def test_rejected_attempt_preserves_completed_pages_across_host_restart(
        self,
    ) -> None:
        agent = self.start_queries([QUERY, dict(QUERY, kind="model_output")])
        event = self.question("Needs older evidence and model judgment")
        clear = agent._clear_execution

        def interrupted(execution: Any, reason: Any) -> None:
            clear(execution, reason)
            raise InterruptedHost()

        commit = self.store.commit

        def legacy_commit(kind: Any, data: Any, **options: Any) -> Any:
            # Pre-fix journals have the individual page caches but no input-wide index.
            if "updates" in options:
                options["updates"] = {
                    key: value
                    for key, value in options["updates"].items()
                    if not key.startswith("context:turn:input:")
                }
            return commit(kind, data, **options)

        with (
            patch.object(self.store, "commit", side_effect=legacy_commit),
            patch.object(agent, "_clear_execution", side_effect=interrupted),
            self.assertRaises(InterruptedHost),
        ):
            agent.receive(event)
        page = self.provider.bundles[1]["history_pages"][0]
        self.assertEqual(self.store.get("model_calls"), 2)
        agent.close()
        self.store.close()
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        agent = hybrid.HybridFixture.start(self, control_actors={"operator": ["retry"]})
        result = agent.recover()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.provider.bundles[2]["history_pages"], [page])
        self.assertEqual(self.store.get("model_calls"), 3)
        self.assertEqual(
            len(
                [row for row in self.store.records() if row["kind"] == "history_query"]
            ),
            1,
        )
        self.assertEqual(self.store.get("state"), {"count": 1})
        self.store._verify()

    def test_each_fresh_input_has_its_own_two_query_allowance(self) -> None:
        agent = self.start_queries([])
        page_counts = []

        def querying(bundle: Any) -> Any:
            page_counts.append(len(bundle["history_pages"]))
            if len(bundle["history_pages"]) < 2:
                self.provider.calls.append("respond")
                self.provider.bundles.append(bundle)
                return {"history_query": QUERY}
            return hybrid.ScriptedModel.respond(self.provider, bundle)

        self.provider.respond = querying
        events = []
        for _ in range(2):
            event = self.question("Needs older evidence")
            events.append(event)
            self.assertEqual(agent.receive(event)["status"], "completed")
        self.assertEqual(self.store.get("state"), {"count": 2})
        self.assertEqual(self.store.get("model_calls"), 6)
        self.assertEqual(page_counts, [0, 1, 2, 0, 1, 2])
        first, second = [agent._input_history_key(event["id"]) for event in events]
        self.assertEqual(len(self.store.get(first)), 2)
        self.assertTrue(self.store.retain(target_bytes=1, minimum_steps=1)["pruned"])
        self.assertIsNone(self.store.get(first))
        self.assertEqual(len(self.store.get(second)), 2)
        self.store._verify()
