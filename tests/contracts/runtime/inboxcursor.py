from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from remote_agent.authority import DurableRoom
from remote_agent.network import HTTPError, RoomAPI
from remote_agent.store import Store


class InboxCursorFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "room.sqlite3"
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())
        self.room = SimpleNamespace(
            store=self.store,
            failed=False,
            lock=threading.RLock(),
            engine=SimpleNamespace(
                closed=False, process=SimpleNamespace(poll=lambda: None)
            ),
            config={"bindings": {"receiver": "agent"}},
        )
        self.api = RoomAPI(cast(DurableRoom, self.room))
        self.grant = {"actor": "agent", "permissions": ["room"]}

    def inbox(self, after: Any, limit: object = 128) -> Any:
        return self.api.dispatch(
            "POST", "/v1/inbox", self.grant, {"after": after, "limit": limit}
        )

    def page(self, cursor: Any, events: Any) -> tuple[Any, ...]:
        return 200, {
            "cursor": cursor,
            "events": events,
            "head": self.store.journal_head(),
        }

    def refuses(self, after: Any, status: int = 409) -> None:
        with self.assertRaises(HTTPError) as caught:
            self.inbox(after)
        self.assertEqual(caught.exception.status, status)
