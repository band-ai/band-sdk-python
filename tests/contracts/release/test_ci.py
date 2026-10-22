"""Require real checker refusals before canonical release promotion."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zipfile import ZipFile

import pytest

from contracts.deploy.releaseverification import verify_release
from tests.contracts.release.buildfixtures import (
    CHECKERS,
    CIHarness,
    command_environment,
)


@pytest.mark.contract_runtime
class TestConformanceGate(TestCase):
    def test_each_broken_checker_fails_ci_before_building_a_release(self) -> None:
        for checker, label in CHECKERS.items():
            with self.subTest(checker=checker), TemporaryDirectory() as directory:
                output, result, failure = CIHarness(
                    Path(directory), broken=checker
                ).exercise()
                self.assertIsNotNone(failure, "CI ignored a broken checker: " + checker)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["gates"][-1]["name"], label)
                self.assertEqual(result["gates"][-1]["exit_code"], 37)
                self.assertTrue((output / result["gates"][-1]["log"]).is_file())
                self.assertFalse((output / "release-a").exists())

    def test_healthy_checkers_run_without_rewriting_tracked_report(self) -> None:
        with TemporaryDirectory() as directory:
            output, result, failure = CIHarness(Path(directory)).exercise()
            self.assertIsNone(failure)
            self.assertEqual(result["status"], "passed")
            gates = {gate["name"]: gate["exit_code"] for gate in result["gates"]}
            for label in CHECKERS.values():
                self.assertEqual(gates.get(label), 0, "missing CI gate: " + label)
            self.assertEqual(
                json.loads((output / "coordination-report.json").read_text()),
                {"passed": True},
            )
            tracked = (
                output / "source/contracts/spec/conformance/coordination-report.json"
            )
            self.assertEqual(tracked.read_text(), "tracked original\n")
            self.assertEqual(
                subprocess.check_output(
                    ["git", "status", "--porcelain"],
                    cwd=output / "source",
                    env=command_environment(),
                    timeout=30,
                ),
                b"",
            )
            first = verify_release(output / "release-a")
            second = verify_release(output / "release-b")
            self.assertEqual(first.manifest, second.manifest)
            self.assertEqual(first.wheel.read_bytes(), second.wheel.read_bytes())
            self.assertIn("requirements-runtime.lock", first.manifest["files"])
            self.assertIn(
                "dependencies/contract_probe-6.0.1-py3-none-any.whl",
                first.manifest["files"],
            )
            with ZipFile(first.wheel) as archive:
                self.assertEqual(
                    {entry.date_time for entry in archive.infolist()},
                    {(1980, 1, 1, 0, 0, 0)},
                )

    def test_failed_band_engine_build_stops_before_runtime_checks_and_release(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            output, result, failure = CIHarness(
                Path(directory), broken_band_engine=True
            ).exercise()
            self.assertIsNotNone(
                failure, "CI never built the engine required by Band runtime tests"
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["gates"][-1]["name"], "band-python-engine-compile")
            self.assertEqual(result["gates"][-1]["exit_code"], 37)
            self.assertNotIn(
                "python-runtime", [gate["name"] for gate in result["gates"]]
            )
            self.assertFalse((output / "release-a").exists())

    def test_unavailable_callback_isolation_stops_before_builds_and_release(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            output, result, failure = CIHarness(
                Path(directory), broken_isolation=True
            ).exercise()
            self.assertIsNotNone(
                failure, "CI did not check callback isolation before qualification"
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["gates"][-1]["name"], "callback-isolation")
            self.assertEqual(result["gates"][-1]["exit_code"], 37)
            self.assertNotIn(
                "header-spikes-compile", [gate["name"] for gate in result["gates"]]
            )
            self.assertFalse((output / "release-a").exists())
