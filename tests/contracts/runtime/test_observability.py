from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from remote_agent.network import AgentAPI
from remote_agent.operations import inspect
from remote_agent.store import Store
from remote_agent.transport import TransportError
from tests.contracts.runtime.hybrid import HybridFixture
from tests.contracts.runtime.observability import InterruptedHost
from tests.contracts.runtime.program import SOURCE


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestObservability(HybridFixture):
    def test_unknown_effect_is_distinct_from_computation_and_counters_survive_restart(
        self,
    ) -> None:
        agent = self.start()
        event = self.question("private-observability-canary")
        publish = self.room.publish

        def lost_reply(*args: Any) -> None:
            publish(*args)
            raise TransportError("private-transport-canary")

        with patch.object(self.room, "publish", lost_reply):
            self.assertEqual(agent.receive(event)["status"], "held")
            metrics = AgentAPI(agent).metrics()
            self.assertTrue(metrics["staged"])
            self.assertIsNone(metrics["execution"])
            self.assertEqual(metrics["send_attempts"], 1)
            self.assertEqual(metrics["events"]["publish_unknown"], 1)
            self.assertEqual(metrics["events"]["publish_attempt"], 1)
            self.assertNotIn("canary", json.dumps(metrics))
            inspected = inspect(self.store.path)["agent"]
            self.assertEqual(inspected["events"], metrics["events"])
            agent.close()
            self.store.close()
            self.store = Store(self.path / "agent.sqlite3")
            self.addCleanup(self.store.close)
        agent = self.start()
        self.assertEqual(agent.recover()["status"], "completed")
        metrics = AgentAPI(agent).metrics()
        self.assertFalse(metrics["staged"])
        self.assertEqual(metrics["send_attempts"], 0)
        self.assertEqual(metrics["events"]["publish_unknown"], 1)
        self.assertEqual(metrics["events"]["publish_attempt"], 2)
        self.assertEqual(metrics["model_calls"], 1)

    def test_unresolved_model_is_reported_without_input_or_callback_details(
        self,
    ) -> None:
        agent = self.start()
        commit = self.store.commit

        def interrupt(kind: Any, *args: Any, **kwargs: Any) -> Any:
            result = commit(kind, *args, **kwargs)
            if kind == "model_input":
                raise InterruptedHost()
            return result

        with (
            patch.object(self.store, "commit", side_effect=interrupt),
            self.assertRaises(InterruptedHost),
        ):
            agent.receive(self.question("private-prompt-canary"))
        self.assertEqual(agent.recover()["status"], "held")
        metrics = AgentAPI(agent).metrics()
        self.assertEqual(metrics["execution"], {"producer": "llm", "status": "started"})
        self.assertFalse(metrics["staged"])
        self.assertEqual(metrics["events"]["turn_held"], 1)
        self.assertNotIn("canary", json.dumps(metrics))
        self.assertEqual(self.provider.calls, [])

    def test_live_repair_progress_and_installation_are_visible_without_fault_contents(
        self,
    ) -> None:
        self.provider.candidate = self.initial
        agent = self.start(
            SOURCE.replace('Message("answer"', 'Message("question"'),
            validator=lambda candidate, bundle: {
                "ok": True,
                "checks": ["fixture semantics"],
            },
        )
        try:
            self.assertEqual(agent.receive(self.question())["status"], "completed")
            self.assertTrue(self.provider.repair_started.wait(2))
            metrics = AgentAPI(agent).metrics()
            self.assertEqual(metrics["repair_status"], "running")
            self.assertEqual(metrics["events"]["fault"], 1)
            self.assertTrue(metrics["quarantined"])
        finally:
            self.provider.release_repair.set()
        self.assertTrue(agent.wait_for_repair(3))
        metrics = AgentAPI(agent).metrics()
        self.assertEqual(metrics["repair_status"], "installed")
        self.assertEqual(metrics["events"]["program_installed"], 1)
        self.assertFalse(metrics["quarantined"])
        self.assertNotIn("reason", json.dumps(metrics))
