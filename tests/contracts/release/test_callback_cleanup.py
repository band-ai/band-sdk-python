"""L16 residual: qualification escalation must also reap callback sessions."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from contracts.deploy import qualificationprocess
from tests.contracts.release.processes import TimeoutAtReady, live

DRIVER = r"""
import os, signal, sys
from pathlib import Path
from remote_agent.program import Artifact, Program
from remote_agent.providers import CommandProvider, CommandValidator
root = Path(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
artifact = Artifact.publish('# qualification callback fixture', root / 'artifacts')
argv = [sys.executable, str(root / 'worker.py'), str(root / 'child.json')]
if sys.argv[2] == 'provider':
    CommandProvider(argv, root / 'artifacts', timeout=60).execute({}, timeout=60)
else:
    CommandValidator(argv, timeout=60)(Program(artifact), {})
"""

WORKER = r"""
import json, os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(json.dumps({'pid': os.getpid(), 'group': os.getpgrp(),
                                       'environment_keys': sorted(os.environ)}))
signal.pause()
"""


class TestCallbackQualificationCleanup(unittest.TestCase):
    @pytest.mark.contract_linux
    def test_forced_driver_escalation_sweeps_real_provider_and_validator_groups(
        self,
    ) -> None:
        for kind in ("provider", "validator"):
            with (
                self.subTest(kind=kind),
                tempfile.TemporaryDirectory(prefix="callback-cleanup-") as directory,
            ):
                root = Path(directory)
                (root / "driver.py").write_text(DRIVER)
                (root / "worker.py").write_text(WORKER)
                child = None
                try:
                    with (
                        TimeoutAtReady(root / "child.json", force=True),
                        self.assertRaises(subprocess.TimeoutExpired),
                    ):
                        qualificationprocess.run_qualification(
                            [sys.executable, str(root / "driver.py"), str(root), kind],
                            timeout=30,
                            cleanup_timeout=2,
                        )
                    self.assertTrue(
                        (root / "child.json").exists(),
                        "callback never reached actual child spawn",
                    )
                    child = json.loads((root / "child.json").read_text())
                    self.assertEqual(child["pid"], child["group"])
                    self.assertNotIn(
                        qualificationprocess.PROCESS_LOG, child["environment_keys"]
                    )
                    survivors = [child["pid"]] if live(child["pid"]) else []
                    self.assertEqual(
                        survivors, [], "unregistered callback survived outer escalation"
                    )
                finally:
                    if child is None and (root / "child.json").exists():
                        child = json.loads((root / "child.json").read_text())
                    if child and live(child["pid"]):
                        os.killpg(child["group"], signal.SIGKILL)

    def test_sanitized_qualification_service_environment_keeps_only_registry_control(
        self,
    ) -> None:
        key = qualificationprocess.PROCESS_LOG
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    key: str(Path(directory) / "processes.jsonl"),
                    "UNRELATED_SECRET": "synthetic",
                },
                clear=True,
            ),
        ):
            base = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
            environment = getattr(
                qualificationprocess, "qualification_environment", dict
            )(base)
            self.assertEqual(
                environment,
                dict(base, **{key: str(Path(directory) / "processes.jsonl")}),
            )
            self.assertEqual(base, {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                getattr(qualificationprocess, "qualification_environment", dict)(base),
                base,
            )
