from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from remote_agent.modeljournal import ModelJournal
from remote_agent.store import Store
from tests.contracts.runtime.operations import InterruptedReplay, OperationsFixture


class TestOperations(OperationsFixture):
    @pytest.mark.contract_runtime
    def test_live_backup_and_inspection_do_not_take_the_service_ownership_lock(
        self,
    ) -> None:
        self.store.commit(
            "private",
            {"payload": "private-sentinel"},
            updates={"mode": "program", "turns": 2},
        )
        result = self.cli("inspect", self.store.path)
        self.assertEqual(result["journal"]["records"], 1)
        self.assertNotIn("private-sentinel", json.dumps(result))
        backup = self.root / "backup.sqlite3"
        result = self.cli("backup", self.store.path, backup)
        self.assertEqual(result["records"], 1)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.store.commit("after_backup", {}, updates={"turns": 3})
        restored = self.root / "restore/state.sqlite3"
        self.cli("restore", backup, restored)
        with Store(restored) as store:
            self.assertEqual(store.get("turns"), 2)
        self.cli("restore", backup, restored, code=2)

    @pytest.mark.contract_isolation
    def test_export_and_program_replay_use_recorded_io_after_the_artifact_is_removed(
        self,
    ) -> None:
        self.program.describe({}, invocation_id="initial")
        event = {"id": "q", "payload": {"text": "ping"}}
        expected = self.program.handle(event, {"count": 0}, {}, invocation_id="turn-1")
        archive = self.root / "journal.jsonl"
        self.cli("export", self.store.path, archive)
        Path(self.artifact.path).unlink()
        output = self.root / "replayed.json"
        self.cli("replay", archive, "turn-1", output)
        result = json.loads(output.read_text())
        self.assertEqual(result["result"], expected)
        self.assertEqual(result["input"]["request"]["event"], event)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(self.store.records()), 4)

    @pytest.mark.contract_runtime
    def test_interrupted_replay_remains_unresolved_and_corrupt_exports_are_refused(
        self,
    ) -> None:
        with (
            patch.object(self.program, "execute", side_effect=InterruptedReplay),
            self.assertRaises(InterruptedReplay),
        ):
            self.program.describe({}, invocation_id="interrupted")
        archive = self.root / "journal.jsonl"
        self.cli("export", self.store.path, archive)
        output = self.root / "unresolved.json"
        self.cli("replay", archive, "interrupted", output, code=3)
        self.assertEqual(json.loads(output.read_text())["status"], "started")
        self.assertIsNone(json.loads(output.read_text())["execution"])
        archive.write_text(
            archive.read_text().replace('"purpose":"validation"', '"purpose":"changed"')
        )
        rejected = self.root / "rejected.json"
        self.cli("replay", archive, "interrupted", rejected, code=2)
        self.assertFalse(rejected.exists())

    @pytest.mark.contract_linux
    def test_live_capacity_metrics_report_record_and_database_pressure(self) -> None:
        self.store.max_records = 2
        self.store.commit("one", {})
        metrics = self.store.metrics()
        self.assertEqual(
            (metrics["records"], metrics["record_limit"], metrics["record_percent"]),
            (1, 2, 50),
        )
        self.assertGreater(metrics["database_bytes"], 0)
        self.store.commit("two", {})
        self.assertEqual(self.store.metrics()["pressure"], "critical")

    @pytest.mark.contract_runtime
    def test_model_replay_returns_a_recorded_result_without_the_provider(self) -> None:
        journal = ModelJournal(self.store)
        journal.run(
            "model1",
            "respond",
            {"name": "fixture"},
            lambda: {"request": "recorded prompt"},
            lambda _: {
                "profile": "remote-agent/callback-result/1",
                "value": {"answer": "recorded"},
                "failure": None,
                "uncertain": False,
            },
            max_calls=1,
            timeout_ms=1000,
        )
        archive, output = self.root / "model.jsonl", self.root / "model.json"
        self.cli("export", self.store.path, archive)
        self.cli("replay", archive, "model1", output, "--kind", "model")
        result = json.loads(output.read_text())
        self.assertEqual(result["result"], {"answer": "recorded"})
        self.assertEqual(result["input"]["request"], {"request": "recorded prompt"})
        self.assertEqual(self.store.get("model_calls"), 1)
