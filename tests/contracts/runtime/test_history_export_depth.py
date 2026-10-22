"""Replay valid controller history and retained anchors without resampling."""

from __future__ import annotations

import json

import pytest

from remote_agent.operations import export, recorded, replay
from tests.contracts.runtime.historydepth import HistoryDepthFixture, container_depth


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestHistoryExportDepth(HistoryDepthFixture):
    def test_boundary_wake_produces_a_real_deep_history_request(self) -> None:
        frame = self.history_record()
        self.assertEqual(container_depth(frame), 138)
        self.assertEqual(container_depth(self.store.search(kind="model_input")), 140)

    def test_export_replays_the_history_request_and_captured_result(self) -> None:
        frame = self.history_record()
        archive = self.path / "history.jsonl"
        export(self.store.path, archive)
        identifier = frame["data"]["id"]
        value = recorded(archive, identifier, kind="model")
        self.assertEqual(value["input"], frame["data"])
        self.assertEqual(value["result"], self.recorded_result(frame))
        self.assertTrue(value["replayable"])
        destination = self.path / "recorded.json"
        self.assertEqual(
            replay(archive, identifier, destination, kind="model"),
            {"status": "completed", "replayable": True},
        )
        self.assertEqual(json.loads(destination.read_text()), value)
        self.assertEqual(self.model.calls, ["wake", "respond", "respond"])

    def test_retained_anchor_preserves_accepted_deep_state_during_replay(self) -> None:
        frame = self.retained_record()
        archive = self.path / "retained.jsonl"
        header = export(self.store.path, archive)
        self.assertEqual(header["profile"], "remote-agent/journal-export/2")
        self.assertGreater(container_depth(header), 128)
        value = recorded(archive, frame["data"]["id"], kind="model")
        self.assertEqual(value["input"], frame["data"])
        self.assertEqual(value["result"], self.recorded_result(frame))
        self.assertEqual(value["journal_head"], self.store.journal_head()["hash"])
        self.assertEqual(self.model.calls, ["respond", "respond"])
