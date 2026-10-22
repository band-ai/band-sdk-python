from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from remote_agent.hostkey import HostKey
from remote_agent.storage import StoreError
from remote_agent.store import Store
from tests.contracts.runtime.required import require_value
from tests.contracts.runtime.store import StoreFixture


class TestStore(StoreFixture):
    def test_ephemeral_mode_keeps_execution_state_and_host_identity_only_in_memory(
        self,
    ) -> None:
        ephemeral_path = Path(self.directory.name) / "never-created" / "agent.sqlite3"
        with Store(ephemeral_path, durable=False) as store:
            store.commit(
                "turn",
                {"input": "private"},
                updates={"state": {"n": 1}},
                snapshot={"program": "p", "schema": "1", "state": {"n": 1}},
            )
            host = HostKey(store)
            first = host.identifier("session", "Agent:a", "input", 1)
            self.assertEqual(first, host.identifier("session", "Agent:a", "input", 1))
            self.assertEqual(store.get("state"), {"n": 1})
            self.assertEqual(
                require_value(store.latest_snapshot(), "Expected a committed snapshot")[
                    "state"
                ],
                {"n": 1},
            )
            self.assertFalse(ephemeral_path.parent.exists())
            with self.assertRaisesRegex(StoreError, "Ephemeral"):
                store.backup(Path(self.directory.name) / "forbidden.sqlite3")
        with Store(ephemeral_path, durable=False) as restarted:
            self.assertEqual(restarted.records(), [])
            self.assertIsNone(restarted.get("state"))
            self.assertNotEqual(
                first, HostKey(restarted).identifier("session", "Agent:a", "input", 1)
            )
        self.assertFalse(ephemeral_path.parent.exists())

    @pytest.mark.contract_linux
    def test_search_pages_pin_a_head_and_find_literal_values_without_loading_every_record(
        self,
    ) -> None:
        store = self.open()
        store.commit("input", {"text": "tax 10%_quote' café"})
        store.commit(
            "turn", {"text": "other"}, updates={"state": {"note": "10%_quote'"}}
        )
        store.commit("input", {"text": "tax 10%_quote' again"})
        first = store.search(query="10%_quote'", kind="input", limit=1)
        self.assertEqual([r["sequence"] for r in first["records"]], [1])
        self.assertTrue(first["has_more"])
        self.assertEqual(first["through"], 3)
        store.commit("input", {"text": "10%_quote' arrived later"})
        second = store.search(
            query="10%_quote'",
            kind="input",
            after=first["cursor"],
            through=first["through"],
            limit=1,
        )
        self.assertEqual([r["sequence"] for r in second["records"]], [3])
        self.assertFalse(second["has_more"])
        self.assertEqual(len(store.search(query="10%_quote'")["records"]), 4)
        self.assertEqual(len(store.search(query="café")["records"]), 1)
        self.assertEqual(store.search(query="missing")["records"], [])

    @pytest.mark.contract_linux
    def test_search_rejects_invalid_pages_and_bounds_response_bytes_without_skipping_matches(
        self,
    ) -> None:
        store = self.open(max_record_bytes=2_000_000)
        for bad in [
            {"after": True},
            {"limit": 0},
            {"limit": 101},
            {"through": -1},
            {"kind": ""},
            {"query": "x" * 257},
            {"query": 1},
        ]:
            with self.assertRaises(StoreError):
                store.search(**bad)
        store.commit("input", {"text": "a" * 1_200_000})
        store.commit("input", {"text": "b" * 1_200_000})
        first = store.search(limit=100)
        self.assertEqual(len(first["records"]), 1)
        self.assertTrue(first["has_more"])
        second = store.search(after=first["cursor"], through=first["through"])
        self.assertEqual([r["sequence"] for r in second["records"]], [2])
        self.assertFalse(second["has_more"])
        with self.assertRaises(StoreError):
            store.search(through=3)

    @pytest.mark.contract_linux
    def test_journal_state_and_snapshot_share_one_database_and_transaction(
        self,
    ) -> None:
        store = self.open()
        frame = store.commit(
            "turn",
            {"id": "i1", "state": {"total": 3}},
            updates={"state": {"total": 3}},
            snapshot={"program": "p1", "schema": "1", "state": {"total": 3}},
        )
        self.assertEqual(frame["sequence"], 1)
        self.assertEqual(store.get("state"), {"total": 3})
        self.assertEqual(
            require_value(store.latest_snapshot(), "Expected a committed snapshot")[
                "state"
            ],
            {"total": 3},
        )
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM journal").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM snapshots").fetchone()[0], 1
            )

    @pytest.mark.contract_linux
    def test_snapshot_failure_rolls_back_journal_and_current_state(self) -> None:
        store = self.open()
        store.commit("initial", {}, updates={"state": {"total": 0}})
        with (
            patch.object(
                store, "_snapshot", side_effect=RuntimeError("injected failure")
            ),
            self.assertRaises(RuntimeError),
        ):
            store.commit(
                "turn",
                {},
                updates={"state": {"total": 5}},
                snapshot={"program": "p", "schema": "1", "state": {"total": 5}},
            )
        self.assertEqual(len(store.records()), 1)
        self.assertEqual(store.get("state"), {"total": 0})
        self.assertIsNone(store.latest_snapshot())

    @pytest.mark.contract_linux
    def test_reopen_restores_committed_records_and_snapshot(self) -> None:
        store = self.open()
        store.commit("input", {"id": "first"})
        store.commit(
            "turn",
            {"id": "first"},
            snapshot={"program": "p", "schema": "1", "state": {"n": 1}},
        )
        store.commit("input", {"id": "second"})
        store.close()
        reopened = self.open()
        self.assertEqual([x["sequence"] for x in reopened.records()], [1, 2, 3])
        self.assertEqual(
            require_value(reopened.latest_snapshot(), "Expected a committed snapshot")[
                "sequence"
            ],
            2,
        )
        self.assertEqual(len(reopened.records(after=2)), 1)

    @pytest.mark.contract_linux
    def test_completed_journal_corruption_fails_closed(self) -> None:
        store = self.open()
        store.commit("input", {"id": "one"})
        store.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE journal SET data=?", (json.dumps({"id": "altered"}),)
            )
        with self.assertRaisesRegex(StoreError, "journal"):
            self.open()

    @pytest.mark.contract_linux
    def test_corrupt_snapshot_is_detected_without_losing_the_journal(self) -> None:
        store = self.open()
        store.commit(
            "turn",
            {"state": {"n": 1}},
            snapshot={"program": "p", "schema": "1", "state": {"n": 1}},
        )
        store.db.execute("UPDATE snapshots SET data='{}'")
        with self.assertRaisesRegex(StoreError, "snapshot"):
            store.latest_snapshot()
        self.assertEqual(store.records()[0]["data"]["state"], {"n": 1})

    @pytest.mark.contract_linux
    def test_second_controller_cannot_own_the_same_database(self) -> None:
        store = self.open()
        with self.assertRaisesRegex(StoreError, "owner"):
            Store(self.path)
        store.close()
        self.open()

    @pytest.mark.contract_linux
    def test_invalid_json_and_size_limits_leave_no_partial_change(self) -> None:
        store = self.open(max_record_bytes=100, max_records=2)
        for invalid in [
            {"n": 1.2},
            {"n": float("nan")},
            {"n": 2**63},
            {1: "non-string key"},
        ]:
            with self.assertRaises(StoreError):
                store.commit("bad", invalid)
        with self.assertRaises(StoreError):
            store.commit("big", {"text": "x" * 200})
        store.commit("one", {})
        store.commit("two", {})
        with self.assertRaisesRegex(StoreError, "capacity"):
            store.commit("three", {}, updates={"lost": True})
        self.assertEqual(len(store.records()), 2)
        self.assertIsNone(store.get("lost"))

    @pytest.mark.contract_linux
    def test_process_death_rolls_back_an_open_transaction_and_releases_ownership(
        self,
    ) -> None:
        store = self.open()
        store.commit("before", {})
        store.close()
        script = """
import os,sys
from remote_agent.store import Store
s=Store(sys.argv[1])
s.db.execute('BEGIN IMMEDIATE')
s._append('unfinished', {'n':1})
os._exit(9)
"""
        child = subprocess.run(
            [sys.executable, "-c", script, str(self.path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(child.returncode, 9, child.stderr)
        reopened = self.open()
        self.assertEqual([r["kind"] for r in reopened.records()], ["before"])

    @pytest.mark.contract_linux
    def test_database_capacity_failure_keeps_the_last_committed_state(self) -> None:
        store = self.open(max_database_bytes=65_536)
        store.commit("initial", {}, updates={"state": {"n": 0}})
        with self.assertRaises(StoreError):
            store.commit(
                "too_large", {"text": "x" * 200_000}, updates={"state": {"n": 1}}
            )
        self.assertEqual(store.get("state"), {"n": 0})
        self.assertEqual(len(store.records()), 1)

    @pytest.mark.contract_linux
    def test_backup_contains_a_consistent_database_including_recent_wal_writes(
        self,
    ) -> None:
        store = self.open()
        store.commit(
            "turn",
            {"n": 1},
            snapshot={"program": "p", "schema": "1", "state": {"n": 1}},
        )
        backup = Path(self.directory.name) / "copy.sqlite3"
        store.backup(backup)
        with Store(backup) as copy:
            self.assertEqual(copy.records(), store.records())
            self.assertEqual(copy.latest_snapshot(), store.latest_snapshot())
