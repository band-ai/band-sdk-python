"""Refuse invalid source and toolchains before canonical release construction."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import pytest

from tests.contracts.release.buildfixtures import (
    build_source,
    command_environment,
    unpatched_toolchain,
)


class TestBuildSource(TestCase):
    @pytest.mark.contract_runtime
    def test_unpatched_otp_is_rejected_before_build_or_ci_work(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = build_source(workspace / "source")
            environment = unpatched_toolchain(workspace / "bin")
            for module in ("buildrelease", "ci"):
                with self.subTest(entrypoint=module):
                    destination = workspace / (module + "-output")
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "contracts.deploy." + module,
                            str(destination),
                        ],
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("OTP >=27.3.4.17,<28", result.stderr)
                    self.assertFalse(
                        destination.exists(), "unsupported build created output"
                    )

    def test_dirty_source_is_rejected_before_creating_a_release(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = build_source(workspace / "source")
            marker = root / "tracked.txt"
            for change in ("tracked", "untracked"):
                with self.subTest(change=change):
                    if change == "tracked":
                        marker.write_text("uncommitted\n", encoding="utf-8")
                    else:
                        marker.write_text("committed\n", encoding="utf-8")
                        (root / "untracked.txt").write_text(
                            "uncommitted\n", encoding="utf-8"
                        )
                    destination = workspace / ("release-" + change)
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "contracts.deploy.buildrelease",
                            str(destination),
                        ],
                        cwd=root,
                        env=command_environment(),
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(
                        destination.exists(), "dirty build created a release directory"
                    )
                    self.assertIn("clean source", result.stderr)
