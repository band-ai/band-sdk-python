from __future__ import annotations

from unittest.mock import patch

import pytest

from remote_agent.network import HTTPRoom
from remote_agent.service import Service
from remote_agent.transport import TransportError
from tests.contracts.runtime.inboxadmission.kit import InboxAdmissionFixture


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestInboxAdmission(InboxAdmissionFixture):
    def test_late_duplicate_and_lost_pushes_never_reorder_the_inbox(self) -> None:
        room, service, config = self.services()
        client = HTTPRoom(room.http.url, "u" * 32)
        first = self.note(client, "q1", 0)
        second = self.note(client, "q2", 1)
        records = service.store.records()
        # q1 push is lost; q2 arrives twice first, then q1 arrives late.
        for event in (second, second, first):
            self.assertEqual(self.push(service, event), (200, {"status": "notified"}))
        self.assertEqual(service.store.records(), records)
        self.assertEqual(service.store.get("queue"), [])
        self.assertEqual(service.store.get("turns"), 0)
        service.cycle()
        self.assertEqual(service.store.get("state"), {"order": ["q1", "q2"]})
        self.assertEqual(service.store.get("turns"), 2)
        service.cycle()
        self.assertEqual(service.store.get("turns"), 2)
        # A notification and wake need not survive restart: the cursor does.
        service.close()
        self.note(client, "q3", 2)
        recovered = Service(config)
        self.addCleanup(recovered.close)
        recovered.cycle()
        self.assertEqual(recovered.store.get("state"), {"order": ["q1", "q2", "q3"]})

    def test_notification_preserves_held_work_and_rejects_unverified_facts(
        self,
    ) -> None:
        room, service, _ = self.services()
        client = HTTPRoom(room.http.url, "u" * 32)
        event = self.note(client, "q1", 0)
        service.held = True
        with patch.object(
            service.agent, "recover", side_effect=AssertionError("push recovered work")
        ):
            self.assertEqual(service.notify(event), {"status": "notified"})
        self.assertTrue(service.held)
        service.poll_wake.clear()
        before = service.store.records()
        with self.assertRaises(TransportError):
            service.notify(dict(event, payload={"text": "forged"}))
        self.assertFalse(service.poll_wake.is_set())
        self.assertEqual(service.store.records(), before)
