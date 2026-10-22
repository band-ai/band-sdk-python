from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from remote_agent.store import Store
from tests.contracts.runtime import hybrid
from tests.contracts.runtime.required import require_value

EXHAUSTED = {"status": "held", "reason": "Response attempt budget exhausted"}


DISCARDED = {"status": "completed", "producer": "supervisor", "receipt": None}


class TerminalBudgetFixture(hybrid.HybridFixture):
    def setUp(self) -> None:
        hybrid.HybridFixture.setUp(self)
        self.clock = [0]

    def start_budgeted(self, *, max_attempts: int = 1) -> Any:
        self.agent = hybrid.HybridAgent(
            self.store,
            hybrid.Program(self.initial, timeout=0.4),
            self.room,
            self.gate,
            self.provider,
            actor="agent",
            now=lambda: self.clock[0],
            snapshot_every=2,
            max_attempts=max_attempts,
            control_actors={"operator": ["retry"]},
            artifact_directory=self.path / "artifacts",
        )
        self.addCleanup(self.agent.close)
        return self.agent

    def reopen(self, agent: Any, *, max_attempts: int = 1) -> Any:
        agent.close()
        self.store.close()
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        return self.start_budgeted(max_attempts=max_attempts)

    def exhausted(self, *, failure: str = "history") -> tuple[Any, ...]:
        def reject(bundle: Any) -> dict[str, Any]:
            self.provider.calls.append("respond")
            self.provider.bundles.append(bundle)
            if failure == "history":
                return {
                    "history_query": {
                        "after": 0,
                        "limit": 21,
                        "kind": "turn",
                        "query": "",
                    }
                }
            return {"state": {"count": 99}, "message": None, "unexpected": True}

        self.provider.respond = reject
        agent = self.start_budgeted()
        event = self.question("Needs history and judgment")
        self.assertEqual(agent.receive(event), EXHAUSTED)
        self.assertEqual(self.store.get("attempts:" + event["id"]), 1)
        self.assertEqual(self.store.get("model_calls"), 1)
        self.assertIsNone(self.store.get("execution"))
        self.assertIsNone(self.store.get("staged"))
        self.assertEqual(self.store.get("queue"), [event["id"]])
        rejected = [r for r in self.store.records() if r["kind"] == "llm_rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertIn(
            "invalid_history_query" if failure == "history" else "invalid_model_reply",
            rejected[0]["data"]["reason"],
        )
        return agent, event

    def expire(self) -> Any:
        self.clock[0] = 1_000_000_000
        view = self.room.view("agent", self.clock[0])["view"]
        self.assertEqual(view["status"], "terminal")
        self.assertEqual(view["obligations"], [])
        return view

    @contextmanager
    def no_work(self) -> Generator[Any, None, None]:
        with (
            patch.object(
                self.provider, "respond", side_effect=AssertionError("extra model")
            ) as model,
            patch.object(
                hybrid.Program, "execute", side_effect=AssertionError("extra program")
            ) as program,
            patch.object(
                self.room, "publish", side_effect=AssertionError("extra publication")
            ) as publish,
            patch.object(
                self.room, "lookup", side_effect=AssertionError("extra lookup")
            ) as lookup,
        ):
            yield
            for callback in (model, program, publish, lookup):
                callback.assert_not_called()

    def assert_discarded(self, agent: Any, event: Any, view: Any, result: Any) -> None:
        self.assertEqual(result, DISCARDED)
        self.assertEqual(self.store.get("queue"), [])
        self.assertIsNone(self.store.get("execution"))
        self.assertIsNone(self.store.get("staged"))
        self.assertEqual(self.store.get("state"), {"count": 0})
        self.assertEqual(
            require_value(
                self.store.latest_snapshot(), "Expected a committed snapshot"
            )["state"],
            {"count": 0},
        )
        self.assertEqual(self.store.get("turns"), 1)
        self.assertEqual(self.store.get("attempts:" + event["id"]), 1)
        self.assertEqual(self.store.get("model_calls"), 1)
        self.assertEqual(self.provider.calls, ["respond"])
        self.assertEqual(
            self.room.view("agent", self.clock[0])["view"]["facts"], view["facts"]
        )
        self.assertEqual(agent.receive(event), DISCARDED)
        self.assertEqual(agent.recover(), {"status": "idle"})
        self.store._verify()
