"""L16: outer timeouts must let inner finally reap its owned service groups."""

from __future__ import annotations

import importlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from contracts.deploy import installation
from contracts.deploy.qualificationprocess import run_qualification
from tests.contracts.release import kit
from tests.contracts.release.processes import TimeoutAtReady, live
from tests.paths import REPO_ROOT

FIXTURE = r"""
import json, os, pathlib, signal, subprocess, sys, time
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
from contracts.deploy.qualificationprocess import register_process
children = []
def terminate(*_):
    raise SystemExit(143)
signal.signal(signal.SIGTERM, terminate)
try:
    for _ in range(3):
        process = subprocess.Popen([sys.executable, '-c', 'import signal; signal.pause()'], start_new_session=True)
        children.append(process)
        register_process(process)
    (root / 'children.json').write_text(json.dumps([p.pid for p in children]))
    signal.pause()
finally:
    for process in children:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    (root / 'cleaned').write_text('inner finally reaped all three children')
"""


@pytest.mark.contract_linux
class TestQualificationTimeout(unittest.TestCase):
    def test_uncooperative_driver_is_killed_and_registered_groups_are_swept(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "inner.py"
            fixture.write_text(
                FIXTURE.replace(
                    "signal.signal(signal.SIGTERM, terminate)",
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                )
            )
            command = [sys.executable, str(fixture), str(root), str(REPO_ROOT)]
            try:
                with (
                    TimeoutAtReady(root / "children.json", force=True),
                    self.assertRaises(subprocess.TimeoutExpired),
                ):
                    run_qualification(command, timeout=30, cleanup_timeout=2)
                children = json.loads((root / "children.json").read_text())
                survivors = [pid for pid in children if live(pid)]
                self.assertEqual(survivors, [])
                self.assertFalse(
                    (root / "cleaned").exists(), "fixture should force outer fallback"
                )
            finally:
                if (root / "children.json").exists():
                    for pid in json.loads((root / "children.json").read_text()):
                        if live(pid):
                            os.killpg(pid, signal.SIGKILL)

    def test_inner_signal_handler_unwinds_finally_and_nonzero_stays_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = (
                "import signal,sys;from pathlib import Path;sys.path.insert(0,"
                + repr(str(REPO_ROOT))
                + ");"
                "from contracts.deploy.qualificationprocess import install_termination_handler;"
                "install_termination_handler();Path("
                + repr(str(root / "ready"))
                + ").touch();\ntry: signal.pause()\nfinally: Path("
                + repr(str(root / "cleaned"))
                + ').write_text("cleanup")'
            )
            with (
                TimeoutAtReady(root / "ready"),
                self.assertRaises(subprocess.TimeoutExpired),
            ):
                run_qualification(
                    [sys.executable, "-I", "-c", code], timeout=30, cleanup_timeout=2
                )
            self.assertTrue((root / "cleaned").exists())
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            run_qualification(
                [sys.executable, "-c", "raise SystemExit(7)"], timeout=2, check=True
            )
        self.assertEqual(raised.exception.returncode, 7)

    def test_real_outer_timeouts_run_inner_cleanup_and_leave_no_live_children(
        self,
    ) -> None:
        for name in ("smokerelease", "qualifylifecycle", "qualifyband"):
            with (
                self.subTest(entrypoint=name),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                release = kit.release(root / "release")
                fixture = root / "inner.py"
                fixture.write_text(FIXTURE)
                command = [sys.executable, str(fixture), str(root), str(REPO_ROOT)]
                module = importlib.import_module("contracts.deploy." + name)
                qualified = module.run_qualification

                def ordinary_run(
                    argv: list[str], **kwargs: Any
                ) -> subprocess.CompletedProcess[Any]:
                    if "pip" in argv:
                        return subprocess.CompletedProcess(argv, 0)
                    raise AssertionError(
                        "Qualification bypassed its supervised process owner"
                    )

                def supervised_run(
                    argv: list[str],
                    *,
                    root: Path = root,
                    qualified: Callable[
                        ..., subprocess.CompletedProcess[Any]
                    ] = qualified,
                    command: list[str] = command,
                    **kwargs: Any,
                ) -> subprocess.CompletedProcess[Any]:
                    with TimeoutAtReady(root / "children.json"):
                        return qualified(
                            command, check=True, timeout=30, cleanup_timeout=3
                        )

                argv = (
                    [name, str(release)]
                    if name == "smokerelease"
                    else [
                        name,
                        "--baseline",
                        str(release),
                        "--candidate",
                        str(release),
                        "--state",
                        str(root / "state"),
                    ]
                    if name == "qualifylifecycle"
                    else [
                        name,
                        "--release",
                        str(release),
                        "--platform",
                        str(root),
                        "--state",
                        str(root / "state"),
                    ]
                )
                try:
                    with (
                        patch.object(sys, "argv", argv),
                        patch("venv.create"),
                        patch.object(
                            installation.subprocess, "run", side_effect=ordinary_run
                        ),
                        patch.object(
                            module,
                            "run_qualification",
                            side_effect=supervised_run,
                            create=True,
                        ),
                        patch.object(module, "PORTS", [], create=True),
                        self.assertRaises(subprocess.TimeoutExpired),
                    ):
                        module.main()
                    self.assertTrue(
                        (root / "children.json").exists(),
                        "inner never spawned fixture children",
                    )
                    children = json.loads((root / "children.json").read_text())
                    survivors = [pid for pid in children if live(pid)]
                    self.assertEqual(
                        survivors, [], "outer timeout orphaned service children"
                    )
                    self.assertTrue(
                        (root / "cleaned").exists(), "inner finally was bypassed"
                    )
                finally:
                    if (root / "children.json").exists():
                        for pid in json.loads((root / "children.json").read_text()):
                            if live(pid):
                                os.killpg(pid, signal.SIGKILL)
