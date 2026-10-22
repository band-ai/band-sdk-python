from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from remote_agent import operations
from remote_agent.invocations import InvocationJournal
from remote_agent.program import Artifact, Program
from remote_agent.retention import logical_bytes
from remote_agent.storage import StoreError
from remote_agent.store import Store
from tests.contracts.runtime.program import SOURCE
from tests.contracts.runtime.required import require_value
from tests.contracts.runtime.retention import RetentionFixture


@pytest.mark.contract_linux
class TestRetention(RetentionFixture):
    def test_logical_bytes_counts_utf8_payload_code_state_and_retention_anchor(
        self,
    ) -> None:
        store = self.fixture()
        state = {"n": 6, "étiquette": "漢字😀"}
        store.commit(
            "mémoire",
            {"text": "漢字😀"},
            updates={"state": state, "clé": "漢字😀", "pinned_source": "# 漢字😀"},
            snapshot={"program": "p", "schema": "1", "state": state},
        )
        for n in (7, 8):
            after = {"n": n, "étiquette": "漢字😀"}
            store.commit(
                "turn",
                {
                    "input": {"id": f"q{n}", "payload": {"text": "漢字😀"}},
                    "input_at": n,
                    "before": state,
                    "state": after,
                },
                updates={"state": after},
                snapshot={"program": "p", "schema": "1", "state": after},
            )
            state = after
        with self.subTest(phase="before retention"):
            self.assertEqual(logical_bytes(store), self.logical_size(store))
        result = store.retain(target_bytes=1, minimum_steps=1)
        self.assertTrue(result["pruned"])
        self.assertEqual(
            require_value(store.anchor(), "Expected a retained-history anchor")[
                "state"
            ]["clé"],
            "漢字😀",
        )
        with self.subTest(phase="Unicode retention anchor"):
            self.assertEqual(logical_bytes(store), self.logical_size(store))
            self.assertEqual(result["logical_bytes"], self.logical_size(store))

    def test_utf8_byte_pressure_prunes_when_character_count_is_below_the_target(
        self,
    ) -> None:
        store = self.fixture()
        for n in (7, 8, 9):
            store.commit(
                "turn",
                {
                    "input": {"id": f"q{n}", "payload": {"text": "😀" * 1024}},
                    "input_at": n,
                    "before": {"n": n - 1},
                    "state": {"n": n},
                },
                updates={"state": {"n": n}},
                snapshot={"program": "p", "schema": "1", "state": {"n": n}},
            )
        characters = self.logical_size(store, encoded=False)
        byte_count = self.logical_size(store)
        target = (characters + byte_count) // 2
        self.assertLess(characters, target)
        self.assertLess(target, byte_count)
        result = store.retain(target_bytes=target, minimum_steps=2)
        self.assertTrue(result["pruned"])
        self.assertEqual(result["retained_steps"], 2)
        self.assertEqual(result["logical_bytes"], self.logical_size(store))
        self.assertEqual(result["over_target"], self.logical_size(store) > target)
        self.assertEqual(store.get("state"), {"n": 9})

    def test_size_pressure_retains_the_step_floor_and_prunes_cached_payloads(
        self,
    ) -> None:
        store = self.fixture()
        head = store.journal_head()
        result = store.retain(target_bytes=1, minimum_steps=2)
        self.assertTrue(result["pruned"])
        self.assertEqual(result["retained_steps"], 2)
        self.assertTrue(result["over_target"])
        self.assertEqual(store.journal_head(), head)
        self.assertEqual(
            [r["data"]["input"]["id"] for r in store.records() if r["kind"] == "turn"],
            ["q5", "q6"],
        )
        self.assertIsNone(store.get("input:q1"))
        self.assertIsNone(store.get("invocation:turn:1"))
        self.assertIsNone(store.get("effect:1"))
        self.assertIsNotNone(store.get("input:q5"))
        self.assertEqual(
            require_value(store.anchor(), "Expected a retained-history anchor")[
                "input_floor"
            ]["at"],
            4,
        )
        self.assertNotIn("private-input-1", str(store.anchor()))
        store.close()
        with Store(self.path) as restored:
            self.assertEqual(restored.get("state"), {"n": 6})
            self.assertEqual(
                require_value(
                    restored.latest_snapshot(), "Expected a committed snapshot"
                )["state"],
                {"n": 6},
            )
            self.assertEqual(
                require_value(restored.anchor(), "Expected a retained-history anchor")[
                    "snapshot"
                ]["state"],
                {"n": 4},
            )
            page = restored.search()
            self.assertTrue(page["history_truncated"])
            self.assertEqual(page["earliest_sequence"], 18)
            next_record = restored.commit("operator", {})
            self.assertEqual(next_record["sequence"], head["sequence"] + 1)
            restored._verify()

    def test_pending_work_blocks_pruning_and_bad_limits_fail_without_mutation(
        self,
    ) -> None:
        store = self.fixture()
        before = store.journal_head()
        for updates in (
            {"queue": ["q7"]},
            {"staged": {"id": "unacknowledged"}},
            {"execution": {"id": "unknown"}},
            {"repair": {"status": "running"}},
            {"external_pending": ["effect"]},
        ):
            store.commit("pending", {}, updates=updates)
            with self.assertRaisesRegex(StoreError, "Pending"):
                store.retain(target_bytes=1, minimum_steps=1)
            store.commit(
                "clear",
                {},
                updates={key: [] if key == "queue" else None for key in updates},
            )
        for args in (
            {"target_bytes": 0, "minimum_steps": 1},
            {"target_bytes": 1, "minimum_steps": 0},
            {"target_bytes": True, "minimum_steps": 1},
        ):
            with self.assertRaises(StoreError):
                store.retain(**args)
        self.assertIsNone(store.anchor())
        self.assertEqual(len([r for r in store.records() if r["kind"] == "turn"]), 6)
        self.assertGreater(store.journal_head()["sequence"], before["sequence"])

    def test_anchor_and_tail_corruption_fail_closed_and_backup_preserves_anchor(
        self,
    ) -> None:
        store = self.fixture()
        store.retain(target_bytes=1, minimum_steps=3)
        backup = Path(self.temp.name) / "copy.sqlite3"
        store.backup(backup)
        with Store(backup) as restored:
            self.assertEqual(restored.get("state"), {"n": 6})
            self.assertIsNotNone(restored.anchor())
        store.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE retention_anchor SET data=replace(data,'\"n\":3','\"n\":9')"
            )
        with self.assertRaisesRegex(StoreError, "anchor"):
            Store(self.path)

    def test_export_preserves_the_anchor_and_replays_retained_recorded_io(self) -> None:
        store = self.fixture()
        program = Program(
            Artifact.publish(SOURCE, Path(self.temp.name) / "artifacts"),
            journal=InvocationJournal(store),
        )
        expected = program.handle(
            {"id": "retained", "payload": {"text": "ping"}},
            {"count": 0},
            {},
            invocation_id="retained-call",
        )
        store.retain(target_bytes=1, minimum_steps=2)
        archive = Path(self.temp.name) / "export.jsonl"
        operations.export(store.path, archive)
        value = operations.recorded(archive, "retained-call")
        self.assertEqual(value["result"], expected)
        self.assertEqual(value["input"]["request"]["event"]["id"], "retained")
        self.assertEqual(value["journal_head"], store.journal_head()["hash"])
        self.assertTrue(archive.read_text().startswith('{"anchor":'))

    def test_pruning_failure_rolls_back_prefix_and_anchor_together(self) -> None:
        store = self.fixture()
        original = store.journal_head()
        with (
            patch.object(store, "_verify", side_effect=StoreError("injected")),
            self.assertRaisesRegex(StoreError, "injected"),
        ):
            store.retain(target_bytes=1, minimum_steps=2)
        self.assertIsNone(store.anchor())
        self.assertEqual(store.journal_head(), original)
        self.assertEqual(len([r for r in store.records() if r["kind"] == "turn"]), 6)
        self.assertIsNotNone(store.get("input:q1"))
        store._verify()

    def test_no_pressure_or_insufficient_steps_does_not_remove_history(self) -> None:
        store = self.fixture()
        self.assertFalse(
            store.retain(target_bytes=100_000_000, minimum_steps=2)["pruned"]
        )
        self.assertFalse(store.retain(target_bytes=1, minimum_steps=6)["pruned"])
        self.assertIsNone(store.anchor())
