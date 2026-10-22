"""P-14: a callback registered during a cleanup sweep must not escape it."""

from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

from contracts.deploy import qualificationprocess as qp
from tests.contracts.release.processes import live
from tests.paths import REPO_ROOT

WORKER = r"""
import json, os, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from contracts.deploy.qualificationprocess import register_process
root = Path(sys.argv[1])
def late(*_):
    child = subprocess.Popen([sys.executable, '-c', 'import signal; signal.pause()'], start_new_session=True)
    register_process(child)
    (root / 'late.json').write_text(json.dumps(child.pid))
    os._exit(0)
signal.signal(signal.SIGTERM, late)
(root / 'ready').touch()
signal.pause()
"""
DRIVER = r"""
import subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from contracts.deploy.qualificationprocess import register_process
root = Path(sys.argv[1])
child = subprocess.Popen([sys.executable, str(root / 'worker.py'), str(root), sys.argv[2]], start_new_session=True)
register_process(child)
until = time.monotonic() + 5
while not (root / 'ready').exists():
    if time.monotonic() > until: raise RuntimeError('worker never ready')
    time.sleep(.01)
"""


@pytest.mark.contract_linux
class TestLateCallbackCleanup(unittest.TestCase):
    def test_callback_registered_after_first_term_sweep_is_reaped(self) -> None:
        with tempfile.TemporaryDirectory(prefix="late-callback-") as directory:
            root = Path(directory)
            (root / "worker.py").write_text(WORKER)
            (root / "driver.py").write_text(DRIVER)
            try:
                qp.run_qualification(
                    [
                        sys.executable,
                        str(root / "driver.py"),
                        str(root),
                        str(REPO_ROOT),
                    ],
                    timeout=10,
                    check=True,
                )
                self.assertTrue(
                    (root / "late.json").exists(),
                    "TERM did not create the late callback",
                )
                pid = json.loads((root / "late.json").read_text())
                self.assertFalse(live(pid), "Late registered callback survived cleanup")
            finally:
                if (root / "late.json").exists():
                    pid = json.loads((root / "late.json").read_text())
                    if live(pid):
                        os.killpg(pid, signal.SIGKILL)
