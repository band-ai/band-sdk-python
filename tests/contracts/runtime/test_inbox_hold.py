from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest

from remote_agent.controller import ControlError
from remote_agent.storage import StoreError
from remote_agent.store import Store
from remote_agent.transport import TransportError
from tests.contracts.runtime.inboxhold import InboxHoldFixture


@pytest.mark.contract_linux
class TestInboxHold(InboxHoldFixture):
    def test_inbox_409_retains_typed_status_without_parsing_error_prose(self) -> None:
        with self.assertRaises(TransportError) as caught:
            self.client.inbox(5)
        self.assertEqual(getattr(caught.exception, "status", None), 409)
        self.assertEqual(type(caught.exception).__name__, "InboxCursorConflict")

    def test_conflict_holds_durably_and_never_auto_resumes_when_room_grows(
        self,
    ) -> None:
        self.observe_conflict()
        hold = self.store.get("inbox_hold")
        self.assertIsNotNone(hold, "409 must persist a hold before another poll")
        self.assertEqual(
            (hold["session"], hold["actor"], hold["cursor"]), ("s", "agent", 5)
        )
        self.assertFalse(self.service.ready())
        self.assertTrue(self.service.metrics()["inbox_held"])
        self.grow_room()
        before = self.store.records()
        with patch.object(
            self.client, "inbox", side_effect=AssertionError("held client polled")
        ):
            self.service.cycle()
            self.store.close()
            self.store = Store(self.path)
            self.service = self.restart_service()
            self.service.cycle()
        self.assertEqual(self.store.get("inbox_cursor"), 5)
        self.assertEqual(self.store.records(), before)
        self.service.agent.recover.assert_not_called()
        self.service.agent.receive.assert_not_called()

    def test_explicit_reconciliation_replays_from_zero_and_is_idempotent(self) -> None:
        self.observe_conflict()
        event = self.grow_room()
        status, result = self.control()
        self.assertEqual((status, result["status"]), (200, "completed"))
        self.assertEqual(result["cursor"], 0)
        self.assertIsNone(self.store.get("inbox_hold"))
        self.assertEqual(self.store.get("inbox_cursor"), 0)
        self.assertTrue(self.service.ready())
        self.service.cycle()
        self.service.agent.receive.assert_called_once_with(event)
        cursor, records = self.store.get("inbox_cursor"), self.store.records()
        self.assertGreater(cursor, 5)
        self.store.close()
        self.store = Store(self.path)
        self.service = self.restart_service()
        self.assertEqual(self.control(), (status, result))
        self.assertEqual(self.store.get("inbox_cursor"), cursor)
        self.assertEqual(self.store.records(), records)
        with self.assertRaises(ControlError) as caught:
            self.control(action="retry")
        self.assertEqual(caught.exception.status, 409)

    def test_other_controls_and_ungranted_reconciliation_cannot_clear_hold(
        self,
    ) -> None:
        self.observe_conflict()
        hold = self.store.get("inbox_hold")
        self.assertIsNotNone(hold)
        with self.assertRaises(ControlError) as caught:
            self.control(actor="observer")
        self.assertEqual(caught.exception.status, 403)
        # Explicit recovery of retained effects must remain possible; it is
        # separate from authorizing replay of the restored inbox history.
        agent = self.service.agent
        agent._bundle = Mock(return_value={})
        agent._retry_execution = Mock()
        agent._drain = Mock(return_value={"status": "completed"})
        with patch.object(self.client, "view", return_value={"view": {}}):
            status, result = self.control(identifier="retry", action="retry")
        self.assertEqual((status, result["status"]), (200, "completed"))
        agent._retry_execution.assert_called_once()
        self.assertEqual(self.store.get("inbox_hold"), hold)
        self.assertEqual(self.store.get("inbox_cursor"), 5)
        self.service.cycle()
        agent.recover.assert_not_called()

    def test_pending_effect_and_failed_reconciliation_leave_cursor_and_hold_intact(
        self,
    ) -> None:
        self.observe_conflict()
        hold = self.store.get("inbox_hold")
        self.assertIsNotNone(hold)
        for key in ("staged", "execution", "queue"):
            value = ["pending"] if key == "queue" else {"id": "pending"}
            self.store.commit("pending", {}, updates={key: value})
            self.assertEqual(self.control(identifier=key)[1]["status"], "held")
            self.assertEqual(self.store.get(key), value)
            self.store.commit(
                "clear_fixture", {}, updates={key: [] if key == "queue" else None}
            )
        with patch.object(
            self.client, "inbox", side_effect=TransportError("unavailable")
        ):
            self.assertEqual(
                self.control(identifier="unavailable")[1]["status"], "held"
            )
        self.assertEqual(self.store.get("inbox_hold"), hold)
        self.assertEqual(self.store.get("inbox_cursor"), 5)

    def test_failed_hold_commit_is_retried_before_any_later_poll(self) -> None:
        commit = self.store.commit

        def fail(kind: Any, *args: Any, **kwargs: Any) -> Any:
            if kind == "inbox_held":
                raise StoreError("hold commit failed")
            return commit(kind, *args, **kwargs)

        with (
            patch.object(self.store, "commit", side_effect=fail),
            self.assertRaises(StoreError),
        ):
            self.service.cycle()
        self.assertFalse(self.service.ready())
        self.grow_room()
        with patch.object(
            self.client,
            "inbox",
            side_effect=AssertionError("polled before persisting hold"),
        ):
            self.service.cycle()
        self.assertIsNotNone(self.store.get("inbox_hold"))
        self.assertEqual(self.store.get("inbox_cursor"), 5)

    def test_reconciliation_commit_is_atomic_and_transport_failures_are_not_restore_holds(
        self,
    ) -> None:
        with (
            patch.object(self.client, "inbox", side_effect=TransportError("503")),
            self.assertRaises(TransportError),
        ):
            self.service.cycle()
        self.assertIsNone(self.store.get("inbox_hold"))
        self.observe_conflict()
        hold = self.store.get("inbox_hold")
        self.assertIsNotNone(hold)
        commit = self.store.commit

        def fail(kind: Any, *args: Any, **kwargs: Any) -> Any:
            if kind == "inbox_reconciled":
                raise StoreError("commit failed")
            return commit(kind, *args, **kwargs)

        with patch.object(self.store, "commit", side_effect=fail):
            self.assertEqual(self.control()[1]["status"], "held")
        self.assertEqual(self.store.get("inbox_hold"), hold)
        self.assertEqual(self.store.get("inbox_cursor"), 5)
