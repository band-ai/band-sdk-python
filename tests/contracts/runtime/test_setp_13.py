from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from remote_agent.transport import InboxCursorConflict, TransportError
from tests.contracts.runtime.inboxhold import InboxHoldFixture


@pytest.mark.contract_linux
class TestCursorReconciliation(InboxHoldFixture):
    def test_unrelated_or_malformed_inbox_409_is_not_a_restore_hold(self) -> None:
        bodies = (
            {"error": "another conflict"},
            {"code": "cursor_ahead"},
            {"code": "other", "error": "Inbox cursor is ahead of the room journal"},
            None,
        )
        for body in bodies:
            with (
                self.subTest(body=body),
                patch(
                    "remote_agent.network.call",
                    return_value={"status": 409, "body": body},
                ),
            ):
                with self.assertRaises(TransportError) as caught:
                    self.client.inbox(5)
                self.assertNotIsInstance(caught.exception, InboxCursorConflict)

    def test_reconciliation_catches_up_real_room_backlog_in_one_cycle(self) -> None:
        self.observe_conflict()
        for _ in range(512):
            self.room_store.commit("room_started", {})
        event = {"status": "accepted", "id": "after-backlog", "recipients": ["agent"]}
        self.room_store.commit("room_operation", {"result": event})
        self.assertEqual(self.control()[1]["status"], "completed")
        self.service.cycle()
        self.assertEqual(
            self.store.get("inbox_cursor"), self.room_store.journal_head()["sequence"]
        )
        self.service.agent.receive.assert_called_once_with(event)

    def test_catch_up_is_bounded_and_does_not_spin_on_nonadvancing_pages(self) -> None:
        self.store.commit("rewind", {}, updates={"inbox_cursor": 0})

        def page(after: Any, *, limit: Any) -> dict[str, Any]:
            return {"cursor": after + limit, "events": [], "head": {"sequence": 100000}}

        with patch.object(self.client, "inbox", side_effect=page) as inbox:
            self.service.cycle()
        self.assertEqual(inbox.call_count, 16)
        self.assertEqual(self.store.get("inbox_cursor"), 2048)
        with patch.object(
            self.client,
            "inbox",
            return_value={"cursor": 2048, "events": [], "head": {"sequence": 100000}},
        ) as inbox:
            self.service.cycle()
        self.assertEqual(inbox.call_count, 1)
