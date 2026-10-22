from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from remote_agent.program import Program
from remote_agent.transport import TransportError
from tests.contracts.runtime.controlrecovery import ControlRecoveryFixture


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestControlRecovery(ControlRecoveryFixture):
    def test_explicit_takeover_abandons_only_unstaged_program_execution(self) -> None:
        agent = self.start_controlled()
        self.unresolved(agent, "program_output")
        identifier = self.store.get("execution")["id"]
        result = agent.control(
            "operator",
            {
                "id": "take",
                "action": "takeover",
                "reason": "Use the LLM to resolve this turn",
            },
        )
        self.assertEqual(result["status"], "completed")
        with patch.object(
            Program, "execute", side_effect=AssertionError("reran program")
        ):
            self.assertEqual(agent.recover()["status"], "completed")
        self.assertEqual(self.provider.calls, ["wake", "respond"])
        self.assertEqual(
            self.store.get("invocation:" + identifier)["status"], "started"
        )
        self.assertEqual(self.room.view("user", 0)["view"]["version"], 2)

    def test_explicit_retry_can_resample_an_uncertain_model_result_before_any_effect_was_staged(
        self,
    ) -> None:
        agent = self.start_controlled()
        self.unresolved(agent, "model_output", "Use judgment")
        identifier = self.store.get("execution")["id"] + ":respond"
        result = agent.control(
            "operator",
            {
                "id": "retry",
                "action": "retry",
                "reason": "Abandon the uncertain computation and try again",
            },
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.provider.calls, ["respond", "respond"])
        self.assertEqual(self.store.get("model:" + identifier)["status"], "started")
        self.assertEqual(self.store.get("state"), {"count": 1})
        self.assertEqual(self.room.view("user", 0)["view"]["version"], 2)

    def test_resume_cannot_bypass_a_pending_execution(self) -> None:
        agent = self.start_controlled()
        self.unresolved(agent, "program_output")
        pending = self.store.get("execution")
        result = agent.control(
            "operator", {"id": "resume", "action": "resume", "reason": "Resume"}
        )
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.store.get("execution"), pending)
        self.assertEqual(self.provider.calls, [])

    def test_takeover_preserves_an_unknown_room_effect_for_exact_reconciliation(
        self,
    ) -> None:
        agent = self.start_controlled()
        event = self.question()
        publish = self.room.publish

        def lose_ack(*args: Any) -> None:
            publish(*args)
            raise TransportError("acknowledgement lost")

        with patch.object(self.room, "publish", lose_ack):
            self.assertEqual(agent.receive(event)["status"], "held")
            staged = self.store.get("staged")
            agent.control(
                "operator",
                {"id": "take", "action": "takeover", "reason": "Review future turns"},
            )
            self.assertEqual(self.store.get("staged"), staged)
        self.assertEqual(agent.recover()["status"], "completed")
        self.assertEqual(self.provider.calls, ["wake"])
        self.assertEqual(self.room.view("user", 0)["view"]["version"], 2)
