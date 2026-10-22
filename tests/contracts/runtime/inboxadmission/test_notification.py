from __future__ import annotations

from unittest.mock import patch

import pytest

from remote_agent.transport import TransportError
from tests.contracts.runtime.hybrid import HybridFixture


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestNotification(HybridFixture):
    def test_verified_notification_does_not_admit_or_execute_input(self) -> None:
        agent = self.start()
        event = self.question()
        records = self.store.records()
        with patch.object(
            agent, "receive", side_effect=AssertionError("push admitted input")
        ):
            for _ in range(2):
                self.assertEqual(agent.notify(event), {"status": "notified"})
        self.assertEqual(self.store.records(), records)
        self.assertEqual(self.store.get("queue"), [])
        self.assertEqual(self.store.get("turns"), 0)
        self.assertEqual(self.provider.calls, [])
        with self.assertRaises(TransportError):
            agent.notify(dict(event, payload={"text": "forged"}))
        self.assertEqual(self.store.records(), records)
