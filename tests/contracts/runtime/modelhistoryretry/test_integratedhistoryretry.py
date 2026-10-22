from __future__ import annotations

import pytest

from tests.contracts.runtime.hybrid import HybridFixture
from tests.contracts.runtime.modelhistoryretry.kit import AnswerProvider


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestIntegratedHistoryRetry(HybridFixture):
    def test_explicit_retry_completes_the_real_turn_and_room_admission_once(
        self,
    ) -> None:

        provider = AnswerProvider(1)
        agent = self.start(provider=provider, control_actors={"operator": ["retry"]})
        event = self.question("Needs history and model judgment")
        self.assertEqual(agent.receive(event)["status"], "held")
        self.assertEqual(provider.calls, [0, 1])
        command = {
            "id": "retry-history",
            "action": "retry",
            "reason": "Reconcile the history response",
        }
        result = agent.control("operator", command)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["producer"], "llm")
        self.assertEqual(
            result["receipt"]["payload"]["text"], "Recorded recovered answer."
        )
        self.assertEqual(provider.calls, [0, 1, 1])
        self.assertEqual(self.store.get("model_calls"), 3)
        self.assertEqual(self.store.get("state"), {"count": 1})
        self.assertEqual(self.store.get("queue"), [])
        self.assertEqual(self.room.view("user", 0)["view"]["version"], 2)
        self.assertEqual(agent.control("operator", command), result)
        self.assertEqual(agent.receive(event), result)
        self.assertEqual(provider.calls, [0, 1, 1])
        self.store._verify()
