from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from tests.contracts.runtime.repairrecovery import (
    InterruptedHost,
    RepairRecoveryFixture,
)


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestRepairRecovery(RepairRecoveryFixture):
    def test_requested_repair_survives_restart_before_provider_dispatch(self) -> None:
        agent = self.start_repairable()
        self.request(agent)
        agent = self.restart()
        self.provider.release_repair.set()
        agent.recover()
        self.assertTrue(agent.wait_for_repair(3))
        self.assertEqual(self.store.get("repair")["status"], "installed")
        self.assertEqual(self.provider.calls, ["repair"])
        self.assertEqual(self.store.get("repair_calls"), 1)

    def test_completed_generation_is_reused_after_restart_before_validation(
        self,
    ) -> None:
        agent = self.start_repairable()
        repair = self.request(agent, running=True)
        self.provider.release_repair.set()
        agent._call_model("repair", agent._bundle(), identifier=repair["id"])
        self.assertEqual(self.provider.calls, ["repair"])
        agent = self.restart()
        agent.recover()
        self.assertTrue(agent.wait_for_repair(3))
        self.assertEqual(self.store.get("repair")["status"], "installed")
        self.assertEqual(self.provider.calls, ["repair"])
        self.assertEqual(self.store.get("repair_calls"), 1)
        self.assertEqual(self.store.get("model_calls"), 1)

    def test_uncertain_generation_remains_interrupted_without_resampling(self) -> None:
        agent = self.start_repairable()
        repair = self.request(agent, running=True)
        commit = self.store.commit

        def crash(kind: Any, *args: Any, **kwargs: Any) -> Any:
            result = commit(kind, *args, **kwargs)
            if kind == "model_input":
                raise InterruptedHost()
            return result

        with (
            patch.object(self.store, "commit", side_effect=crash),
            self.assertRaises(InterruptedHost),
        ):
            agent._call_model("repair", agent._bundle(), identifier=repair["id"])
        agent = self.restart()
        agent.recover()
        self.assertEqual(self.store.get("repair")["status"], "interrupted")
        self.assertEqual(self.provider.calls, [])

    def test_validated_candidate_waits_for_pending_execution_before_installation(
        self,
    ) -> None:
        agent = self.start_repairable()
        agent.control(
            "operator",
            {"id": "repair", "action": "repair", "reason": "Qualify replacement"},
        )
        self.assertTrue(self.provider.repair_started.wait(2))
        commit = self.store.commit

        def fail_output(kind: Any, *args: Any, **kwargs: Any) -> Any:
            if kind == "program_output":
                raise RuntimeError("result commit failed")
            return commit(kind, *args, **kwargs)

        with patch.object(self.store, "commit", side_effect=fail_output):
            self.assertEqual(agent.receive(self.question())["status"], "held")
        pending = self.store.get("execution")
        self.provider.release_repair.set()
        self.assertTrue(agent.wait_for_repair(3))
        self.assertEqual(self.store.get("program"), self.initial.to_dict())
        self.assertEqual(self.store.get("execution"), pending)
        self.assertEqual(self.store.get("repair")["status"], "waiting")
        agent.control(
            "operator",
            {
                "id": "take",
                "action": "takeover",
                "reason": "Resolve this pure computation",
            },
        )
        agent.recover()
        self.assertTrue(agent.wait_for_repair(3))
        agent.recover()  # A deferred job may have observed the still-running turn.
        self.assertTrue(agent.wait_for_repair(3))
        self.assertEqual(self.store.get("repair")["status"], "installed")
        self.assertEqual(self.provider.calls.count("repair"), 1)
        self.assertEqual(self.store.get("repair_calls"), 1)
        self.assertEqual(self.store.get("mode"), "llm")
