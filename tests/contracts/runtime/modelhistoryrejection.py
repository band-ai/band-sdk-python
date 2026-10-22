from __future__ import annotations

import copy
from typing import Any

from tests.contracts.runtime import hybrid

QUERY = {"after": 0, "limit": 1, "kind": "turn", "query": ""}


class QueryThenAnswer(hybrid.ScriptedModel):
    def __init__(self, queries: Any, *, repeat: bool = False) -> None:
        super().__init__()
        self.queries, self.repeat = queries, repeat

    def respond(self, bundle: Any) -> Any:
        index = self.calls.count("respond")
        if index < len(self.queries) or self.repeat:
            self.calls.append("respond")
            self.bundles.append(copy.deepcopy(bundle))
            return {"history_query": self.queries[min(index, len(self.queries) - 1)]}
        return super().respond(bundle)


class HistoryRejectionFixture(hybrid.HybridFixture):
    def start_queries(
        self, queries: Any, *, repeat: bool = False, **options: Any
    ) -> Any:
        self.provider = QueryThenAnswer(queries, repeat=repeat)
        return hybrid.HybridFixture.start(
            self, control_actors={"operator": ["retry"]}, **options
        )

    def assert_recovers(self, queries: Any, *, calls: int = 2) -> None:
        agent = self.start_queries(queries)
        event = self.question("Needs older evidence and model judgment")
        result = agent.receive(event)
        # Record the actual replay consequence before asserting the desired result.
        if result["status"] == "held":
            restored = agent.recover()
            retried = agent.control(
                "operator",
                {
                    "id": "retry-query",
                    "action": "retry",
                    "reason": "Query result was definitive",
                },
            )
            self.assertEqual(
                (result["status"], restored["status"], retried["status"]),
                ("completed", "idle", "idle"),
                f"Completed query stayed pinned: {result}; model calls={self.store.get('model_calls')}",
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["producer"], "llm")
        self.assertEqual(self.provider.calls.count("respond"), calls)
        self.assertEqual(self.store.get("model_calls"), calls)
        self.assertEqual(self.store.get("attempts:" + event["id"]), 2)
        self.assertIsNone(self.store.get("execution"))
        self.assertEqual(self.store.get("queue"), [])
        self.assertEqual(self.store.get("state"), {"count": 1})
        rejected = [r for r in self.store.records() if r["kind"] == "llm_rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertIn("history", rejected[0]["data"]["reason"].lower())
        outputs = [r for r in self.store.records() if r["kind"] == "model_output"]
        self.assertEqual(len(outputs), calls)
        for frame in outputs:
            capture = self.store.get("model:" + frame["data"]["id"])
            self.assertEqual(capture["status"], "completed")
            self.assertFalse(capture["execution"]["uncertain"])
        self.assertEqual(agent.receive(event), result)
        self.assertEqual(self.provider.calls.count("respond"), calls)
        self.store._verify()


class InterruptedHost(BaseException):
    pass
