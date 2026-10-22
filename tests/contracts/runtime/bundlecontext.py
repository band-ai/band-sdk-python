from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from remote_agent.controller import HybridAgent
from remote_agent.storage import canonical
from remote_agent.store import Store
from remote_agent.transport import ReferenceRoom


class BundleContextFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="bundle-context-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def fixture(self, *, durable: Any, steps: int = 24) -> Any:
        path = self.root / (
            "durable.sqlite3" if durable else "absent/ephemeral.sqlite3"
        )
        store = Store(path, durable=durable)
        self.addCleanup(store.close)
        metadata = {
            "schema": "1",
            "initial": {"n": 0},
            "coverage": {"mode": "full", "handles": ["counter"], "delegates": []},
        }
        initial = {
            "config": {"actor": "Agent:counter"},
            "metadata": metadata,
            "view": {"status": "active"},
        }
        store.commit(
            "initialize",
            initial,
            updates={
                "metadata": metadata,
                "program": {"sha256": "a" * 64},
                "state": {"n": 0},
                "mode": "llm",
                "fault": None,
                "queue": [],
                "staged": None,
                "execution": None,
                "repair": None,
                "turns": 0,
                "model_calls": 0,
            },
            snapshot={"program": "a" * 64, "schema": "1", "state": {"n": 0}},
        )
        for n in range(1, steps + 1):
            event = {
                "id": f"input-{n}",
                "kind": "counter",
                "payload": {"text": "é" * 4500},
            }
            store.commit("input", event)
            store.commit(
                "model_input",
                {
                    "id": f"model-{n}",
                    "operation": "respond",
                    "request": {"private": "x" * 12_000},
                },
            )
            store.commit("model_output", {"id": f"model-{n}", "result": "x" * 12_000})
            store.commit(
                "turn",
                {
                    "input": event,
                    "input_at": n,
                    "before": {"n": n - 1},
                    "state": {"n": n},
                    "message": None,
                    "producer": "llm",
                },
                updates={"state": {"n": n}, "turns": n},
                snapshot={"program": "a" * 64, "schema": "1", "state": {"n": n}},
            )
        agent = HybridAgent.__new__(HybridAgent)
        agent.store, agent.room, agent.actor = (
            store,
            cast(ReferenceRoom, SimpleNamespace(session="s")),
            "Agent:counter",
        )
        agent.initial_view = initial["view"]
        agent.limits = {
            "snapshot_every": 1,
            "max_pending": 16,
            "max_model_calls": 100,
            "max_repairs": 3,
            "max_attempts": 3,
        }
        return agent

    def assert_bounded_bundle(
        self, agent: Any, *, event: Any = None, view: Any = None
    ) -> Any:
        store = agent.store
        records, head, snapshot, anchor = (
            store.records(),
            store.journal_head(),
            store.latest_snapshot(),
            store.anchor(),
        )
        with patch.object(store, "_decode", wraps=store._decode) as decode:
            bundle = agent._bundle(event, view)
        self.assertEqual(bundle["profile"], "remote-agent/sidecar/1")
        self.assertEqual(bundle["input"], event)
        self.assertEqual(bundle["view"], view or agent.initial_view)
        self.assertEqual(bundle["state"], store.get("state"))
        self.assertEqual(bundle["snapshot"], snapshot)
        self.assertEqual(
            bundle["journal_context"],
            {
                "mode": "summary",
                "omitted_records": len(records) - len(bundle["journal_tail"]),
                "retained_from": (anchor or {}).get("sequence", 0) + 1,
            },
        )
        tail = records[-len(bundle["journal_tail"]) :] if bundle["journal_tail"] else []
        self.assertEqual(
            bundle["journal_tail"], [agent._context_record(record) for record in tail]
        )
        self.assertLessEqual(len(canonical(bundle)), 131_072)
        self.assertEqual(store.journal_head(), head)
        self.assertEqual(store.latest_snapshot(), snapshot)
        self.assertEqual(store.anchor(), anchor)
        self.assertEqual(store.records(), records)
        self.assertEqual(
            decode.call_count,
            min(30, len(records)),
            "Building a summary must not decode records outside its last 30",
        )
        return bundle
