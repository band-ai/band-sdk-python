"""AdapterConfig specification regression checks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import pytest

from contracts.spec.tools import generatevectors
from tests.contracts.spec.checkerfailures.kit import ROOT, reference_argv
from tests.contracts.spec.workspace import run_tool


class TestAdapterConfig(unittest.TestCase):
    """A falsy --adapter-config value silently ran the bundled reference adapter and passed (impl M20)."""

    def test_a_falsy_or_malformed_argv_is_refused_before_anything_runs(self) -> None:
        invalid_argv: tuple[Any, ...] = (
            [],
            {},
            0,
            "",
            False,
            [""],
            [1],
            "elixir",
            ["elixir", None],
        )
        for argv in invalid_argv:
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                generatevectors.adapter([{"op": "canonical", "value": 1}], argv)

    def test_check_spec_refuses_a_falsy_adapter_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "adapter.json"
            for value in ([], {}, None, 0, ""):
                config.write_text(json.dumps(value))
                with self.subTest(value=value):
                    result = run_tool(
                        ROOT,
                        "checkspec.py",
                        "--adapter-config",
                        str(config),
                        "--case-prefix",
                        "wire-0",
                    )
                    self.assertNotEqual(result.returncode, 0, result.stdout[-500:])
                    self.assertNotIn('"passed": true', result.stdout)

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine
    def test_the_report_records_the_argv_that_ran(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, output = (
                Path(directory) / "adapter.json",
                Path(directory) / "report.json",
            )
            config.write_text(json.dumps(reference_argv()))
            result = run_tool(
                ROOT,
                "checkspec.py",
                "--adapter-config",
                str(config),
                "--case-prefix",
                "wire-0",
                "--output",
                str(output),
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertEqual(
                json.loads(output.read_text())["adapter"],
                {"kind": "configured", "argv": reference_argv()},
            )
            result = run_tool(
                ROOT, "checkspec.py", "--case-prefix", "wire-0", "--output", str(output)
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertEqual(
                json.loads(output.read_text())["adapter"]["kind"], "reference"
            )
