from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from remote_agent.transport import TransportError
from tests.contracts.runtime import hybrid
from tests.contracts.runtime.terminalbudget import EXHAUSTED, TerminalBudgetFixture


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestTerminalBudget(TerminalBudgetFixture):
    def test_recovery_after_history_rejection_finishes_the_terminal_input(self) -> None:
        agent, event = self.exhausted()
        view = self.expire()
        with self.no_work():
            result = agent.recover()
            self.assert_discarded(agent, event, view, result)

    def test_journal_reopen_after_exhaustion_preserves_terminal_cleanup_and_duplicates(
        self,
    ) -> None:
        agent, event = self.exhausted()
        view = self.expire()
        with self.no_work():
            restored = self.reopen(agent)
            result = restored.recover()
            self.assert_discarded(restored, event, view, result)
            restored = self.reopen(restored)
            self.assertEqual(restored.receive(event), result)
            self.assertEqual(self.store.get("turns"), 1)

    def test_explicit_retry_after_definitive_model_rejection_uses_supervisor_completion(
        self,
    ) -> None:
        agent, event = self.exhausted(failure="reply")
        view = self.expire()
        command = {
            "id": "retry-terminal",
            "action": "retry",
            "reason": "Recheck trusted authority",
        }
        with self.no_work():
            result = agent.control("operator", command)
            self.assert_discarded(agent, event, view, result)
            self.assertEqual(agent.control("operator", command), result)
            self.assertEqual(self.store.get("turns"), 1)

    def test_active_exhausted_input_stays_held_without_spending_more_attempts(
        self,
    ) -> None:
        agent, event = self.exhausted()
        self.assertEqual(self.room.view("agent", 0)["view"]["status"], "active")
        command = {
            "id": "retry-active",
            "action": "retry",
            "reason": "Preserve the exhausted budget",
        }
        with self.no_work():
            self.assertEqual(agent.recover(), EXHAUSTED)
            self.assertEqual(agent.control("operator", command), EXHAUSTED)
            restored = self.reopen(agent)
            self.assertEqual(restored.recover(), EXHAUSTED)
            self.assertEqual(restored.control("operator", command), EXHAUSTED)
        self.assertEqual(self.store.get("queue"), [event["id"]])
        self.assertEqual(self.store.get("attempts:" + event["id"]), 1)
        self.assertEqual(self.store.get("model_calls"), 1)
        self.assertEqual(self.store.get("state"), {"count": 0})
        self.assertEqual(self.store.get("turns"), 0)
        self.store._verify()

    def test_lost_accepted_publication_reconciles_the_exact_proposal_after_expiry(
        self,
    ) -> None:
        agent = self.start_budgeted(max_attempts=2)
        event = self.question("Needs judgment")
        publish = self.room.publish
        accepted = []

        def lose_ack(*args: Any) -> None:
            accepted.append(publish(*args))
            raise TransportError("Acknowledgement lost after acceptance")

        with patch.object(self.room, "publish", side_effect=lose_ack):
            self.assertEqual(agent.receive(event)["status"], "held")
        staged = self.store.get("staged")
        self.assertIsNotNone(staged)
        self.assertEqual(self.store.get("state"), {"count": 0})
        view = self.expire()
        with (
            patch.object(
                self.provider, "respond", side_effect=AssertionError("extra model")
            ) as model,
            patch.object(
                hybrid.Program, "execute", side_effect=AssertionError("extra program")
            ) as program,
            patch.object(
                self.room, "publish", side_effect=AssertionError("new publication")
            ) as new_publish,
            patch.object(self.room, "lookup", side_effect=publish) as lookup,
        ):
            restored = self.reopen(agent, max_attempts=2)
            result = restored.recover()
            lookup.assert_called_once_with(staged["envelope"], "agent", self.clock[0])
            for callback in (model, program, new_publish):
                callback.assert_not_called()
        self.assertEqual(
            result, {"status": "completed", "producer": "llm", "receipt": accepted[0]}
        )
        self.assertEqual(self.store.get("state"), {"count": 1})
        self.assertEqual(self.store.get("model_calls"), 1)
        self.assertEqual(self.store.get("queue"), [])
        self.assertEqual(
            self.room.view("agent", self.clock[0])["view"]["facts"], view["facts"]
        )
        self.assertEqual(restored.receive(event), result)
        self.store._verify()

    def test_unresolved_staged_publication_keeps_its_reconciliation_budget_after_expiry(
        self,
    ) -> None:
        agent = self.start_budgeted()
        event = self.question("Needs judgment")
        with patch.object(
            self.room,
            "publish",
            side_effect=TransportError("Destination outcome unknown"),
        ):
            self.assertEqual(agent.receive(event)["status"], "held")
        staged = self.store.get("staged")
        self.assertIsNotNone(staged)
        self.expire()
        with self.no_work():
            restored = self.reopen(agent)
            self.assertEqual(
                restored.recover(),
                {"status": "held", "reason": "Room reconciliation budget exhausted"},
            )
        self.assertEqual(self.store.get("staged"), staged)
        self.assertEqual(self.store.get("queue"), [event["id"]])
        self.assertEqual(self.store.get("state"), {"count": 0})
        self.assertEqual(self.store.get("model_calls"), 1)
        self.assertEqual(self.store.get("turns"), 0)
        self.store._verify()
