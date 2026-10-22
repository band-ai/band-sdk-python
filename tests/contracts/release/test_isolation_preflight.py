"""A broken real launcher must stop the preflight and preserve its diagnostic."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

from contracts.deploy.environment import BuildSettings
from tests.paths import CONTRACT_DEPLOY_ROOT

SCRIPT = CONTRACT_DEPLOY_ROOT / "checkisolation.py"


class TestIsolationPreflight(unittest.TestCase):
    @pytest.mark.contract_isolation
    def test_real_isolated_python_describe_and_handle_succeed(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"profile": "band-contract/isolation-preflight/1", "passed": True},
        )

    @pytest.mark.contract_system_runtime
    def test_broken_launcher_fails_with_its_bounded_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / "bwrap"
            launcher.write_text(
                "#!/bin/sh\nprintf 'synthetic namespace denial\\n' >&2\nexit 37\n"
            )
            launcher.chmod(0o700)
            environment = BuildSettings().environment([], mix_env="test")
            environment["PATH"] = directory + os.pathsep + os.defpath
            result = subprocess.run(
                [sys.executable, str(SCRIPT)],
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
                check=False,
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["passed"])
        self.assertEqual(report["operation"], "describe")
        self.assertEqual(report["exit_code"], 37)
        self.assertEqual(report["stderr"], "synthetic namespace denial\n")
        self.assertEqual(report["error"], "Callback exited with status 37")
