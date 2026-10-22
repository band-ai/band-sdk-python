from __future__ import annotations

import pytest

from remote_agent.network import HTTPError
from remote_agent.store import Store
from tests.contracts.runtime.inboxcursor import InboxCursorFixture


@pytest.mark.contract_linux
class TestInboxCursor(InboxCursorFixture):
    def test_empty_store_future_cursor_is_refused(self) -> None:
        self.refuses(1)
        self.assertEqual(self.inbox(0), self.page(0, []))

    def test_future_cursor_after_hidden_head_is_refused(self) -> None:
        frame = self.store.commit("room_started", {})
        self.refuses(frame["sequence"] + 1)
        self.assertEqual(
            self.inbox(frame["sequence"]), self.page(frame["sequence"], [])
        )

    def test_future_cursor_cannot_hide_the_next_accepted_receipt(self) -> None:
        head = self.store.commit("room_started", {})["sequence"]
        self.refuses(head + 1)
        # Recover from a legitimate returned cursor, never the refused future position.
        cursor = self.inbox(0)[1]["cursor"]
        accepted = {"status": "accepted", "id": "q1", "recipients": ["agent"]}
        frame = self.store.commit("room_operation", {"result": accepted})
        self.assertEqual(self.inbox(cursor), self.page(frame["sequence"], [accepted]))

    def test_reopened_store_preserves_head_and_cursor_domain(self) -> None:
        head = self.store.commit("room_started", {})["sequence"]
        before = self.inbox(0)
        self.store.close()
        self.store = Store(self.path)
        self.room.store = self.store
        self.assertEqual(self.inbox(0), before)
        self.refuses(head + 1)

    def test_head_is_durable_sequence_not_page_or_event_count(self) -> None:
        for _ in range(3):
            head = self.store.commit("room_started", {})["sequence"]
        self.assertEqual(self.inbox(head), self.page(head, []))
        self.refuses(head + 1)
        self.assertEqual(self.inbox(0, limit=1), self.page(1, []))

    def test_invalid_cursor_bounds_remain_refused(self) -> None:
        for after in (-1, True, 1.5, "1", None, 9_007_199_254_740_992):
            with self.subTest(after=after):
                self.refuses(after, status=400)

    def test_invalid_page_limit_precedes_ahead_of_head_conflict(self) -> None:
        for limit in (0, 129, True, 1.5, "1", None):
            with self.subTest(limit=limit):
                with self.assertRaises(HTTPError) as caught:
                    self.inbox(1, limit=limit)
                self.assertEqual(caught.exception.status, 400)

    def test_malformed_request_fields_remain_bad_request(self) -> None:
        for request in (
            {},
            {"after": 1},
            {"limit": 128},
            {"after": 1, "limit": 128, "actor": "agent"},
        ):
            with self.subTest(request=request):
                with self.assertRaises(HTTPError) as caught:
                    self.api.dispatch("POST", "/v1/inbox", self.grant, request)
                self.assertEqual(caught.exception.status, 400)
