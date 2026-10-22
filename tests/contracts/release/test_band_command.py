"""The qualification command channel tolerates partial writes and crash replay."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from contracts.deploy import qualifyband
from tests.paths import CONTRACT_DEPLOY_ROOT

HERE = CONTRACT_DEPLOY_ROOT


class TestBandCommand(unittest.TestCase):
    def test_driver_never_exposes_truncated_command_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "command-2.json"
            original = '{"id":"old","operation":"revoke"}'
            path.write_text(original)
            replace = os.replace
            observations = []

            def publish(source: str | Path, destination: str | Path) -> None:
                observations.append(path.read_text())
                if os.name == "posix":
                    self.assertEqual(Path(source).stat().st_mode & 0o777, 0o600)
                replace(source, destination)

            with mock.patch.object(os, "replace", publish):
                qualifyband.private(path, {"id": "new", "operation": "restore"})
            self.assertEqual(observations, [original])
            self.assertIn('"new"', path.read_text())

    @pytest.mark.contract_elixir
    def test_host_handles_partial_json_and_replays_ack_without_effect(self) -> None:
        result = subprocess.run(
            ["elixir", "--erl", "+S 2:2", str(HERE / "band_command_test.exs")],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 failures", result.stdout)
