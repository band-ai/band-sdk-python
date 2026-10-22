from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from remote_agent.invocations import InvocationJournal
from remote_agent.operations import export, recorded, replay
from remote_agent.program import Artifact, Program
from remote_agent.protocol import decode
from remote_agent.storage import StoreError
from remote_agent.store import Store
from tests.contracts.runtime.setql8 import nested
from tests.contracts.runtime.setqq2 import capture


class TestExportDepth(unittest.TestCase):
    @pytest.mark.contract_linux
    def test_export_recording_and_replay_accept_callback_depth_boundary(self) -> None:
        for levels in (125, 126):
            with (
                self.subTest(levels=levels),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                with Store(root / "agent.sqlite3") as store:
                    program = Program(
                        Artifact.publish(
                            "# recorded-only fixture\n", root / "artifacts"
                        ),
                        journal=InvocationJournal(store),
                    )
                    expected = {
                        "type": "handled",
                        "state": nested(levels),
                        "message": None,
                    }
                    self.assertEqual(decode(json.dumps(expected)), expected)
                    with patch.object(
                        program, "execute", return_value=capture(expected)
                    ):
                        self.assertEqual(
                            program.handle(
                                {}, expected["state"], {}, invocation_id="deep"
                            ),
                            expected,
                        )
                    archive = root / "export.jsonl"
                    export(store.path, archive)
                    with patch.object(
                        Program,
                        "execute",
                        side_effect=AssertionError("replay ran a program"),
                    ):
                        self.assertEqual(recorded(archive, "deep")["result"], expected)
                        destination = root / "replay.json"
                        self.assertTrue(
                            replay(archive, "deep", destination)["replayable"]
                        )
                    self.assertEqual(
                        json.loads(destination.read_text())["result"], expected
                    )

    def test_callback_depth_limit_is_not_relaxed_by_export_allowance(self) -> None:
        with self.assertRaisesRegex(ValueError, "128 levels"):
            decode(
                json.dumps({"type": "handled", "state": nested(127), "message": None})
            )

    @pytest.mark.contract_linux
    def test_export_depth_allowance_remains_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with Store(root / "agent.sqlite3") as store:
                store.commit("too_deep", nested(134))
                archive = root / "export.jsonl"
                export(store.path, archive)
                with self.assertRaises(StoreError):
                    recorded(archive, "unused")
