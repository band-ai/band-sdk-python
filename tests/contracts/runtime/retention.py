from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.store import Store


class RetentionFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "agent.sqlite3"

    def fixture(self) -> Any:
        store = Store(self.path)
        self.addCleanup(store.close)
        initial = {
            "config": {"actor": "Agent:a"},
            "metadata": {"schema": "1", "initial": {"n": 0}},
            "view": {},
        }
        store.commit(
            "initialize",
            initial,
            updates={
                "state": {"n": 0},
                "program": {"sha256": "p"},
                "metadata": initial["metadata"],
                "queue": [],
                "staged": None,
                "execution": None,
                "repair": None,
            },
            snapshot={"program": "p", "schema": "1", "state": {"n": 0}},
        )
        for n in range(1, 7):
            event = {
                "id": f"q{n}",
                "version": n * 2 - 1,
                "payload": {"text": f"private-input-{n}"},
            }
            store.commit(
                "input",
                event,
                updates={f"input:q{n}": {"event": event, "result": None}},
            )
            store.commit(
                "program_input",
                {"id": f"turn:{n}", "request": {"text": f"private-input-{n}"}},
                updates={f"invocation:turn:{n}": {"status": "started"}},
            )
            store.commit(
                "program_output",
                {"id": f"turn:{n}"},
                updates={
                    f"invocation:turn:{n}": {
                        "status": "completed",
                        "execution": {"text": f"private-output-{n}"},
                    }
                },
            )
            store.commit(
                "turn",
                {
                    "input": event,
                    "input_at": n,
                    "before": {"n": n - 1},
                    "state": {"n": n},
                    "program": "p",
                    "producer": "program",
                    "message": None,
                    "view": {},
                },
                updates={
                    "state": {"n": n},
                    "turns": n,
                    f"input:q{n}": {"event": event, "result": {"status": "completed"}},
                    f"effect:{n}": {
                        "status": "completed",
                        "result": f"private-output-{n}",
                    },
                },
                snapshot={"program": "p", "schema": "1", "state": {"n": n}},
            )
        return store

    def logical_size(self, store: Any, *, encoded: bool = True) -> Any:
        size = lambda value: len(value.encode("utf-8")) if encoded else len(value)
        total = sum(
            size(data) + size(changes) + size(kind) + 144
            for data, changes, kind in store.db.execute(
                "SELECT data,changes,kind FROM journal"
            )
        )
        total += sum(
            size(data) + 144
            for (data,) in store.db.execute("SELECT data FROM snapshots")
        )
        total += sum(
            size(key) + size(value)
            for key, value in store.db.execute("SELECT key,value FROM current_state")
        )
        if store.anchor():
            total += sum(
                size(data) + 64
                for (data,) in store.db.execute("SELECT data FROM retention_anchor")
            )
        return total
