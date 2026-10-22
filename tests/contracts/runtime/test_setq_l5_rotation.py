from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

from remote_agent.hostkey import HostKey
from remote_agent.store import Store
from tests.contracts.runtime.setql5rotation import CRASH


@pytest.mark.contract_linux
class TestRotationSweep(unittest.TestCase):
    def test_killed_partial_and_prepared_rotation_staging_is_swept_on_restart(
        self,
    ) -> None:
        for boundary in ("write", "prepared"):
            with (
                self.subTest(boundary=boundary),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                path = root / "agent.sqlite3"
                with Store(path) as store:
                    host = HostKey(store)
                    host.ensure()
                    original = host.path.read_bytes()
                result = subprocess.run(
                    [sys.executable, "-c", CRASH, str(path), boundary],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 91, result.stderr)
                staged = list(root.glob(".host-key-rotate-*"))
                self.assertEqual(
                    len(staged), 1, "crash fixture did not leave rotation staging"
                )
                with Store(path) as store:
                    host = HostKey(store)
                    host.ensure()
                    self.assertEqual(host.path.read_bytes(), original)
                    self.assertIsNone(store.get("host_key_rotation"))
                    self.assertFalse(
                        list(root.glob(".host-key-rotate-*")),
                        "restart retained staged secret",
                    )

    def test_legacy_rotation_staging_is_removed_without_touching_other_key_namespace(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with Store(root / "agent.sqlite3") as store:
                host = HostKey(store)
                host.ensure()
                legacy = root / ".host-key-rotate-abcd1234"
                legacy.write_bytes(b"legacy staging")
                other_key = root / "other.sqlite3.host-key"
                namespace = hashlib.sha256(os.fsencode(other_key)).hexdigest()[:16]
                other = root / (".host-key-rotate-" + namespace + "-abcd1234")
                other.write_bytes(b"other lifecycle staging")
                host.ensure()
                self.assertFalse(legacy.exists())
                self.assertTrue(
                    other.exists(), "sweep crossed another key lifecycle lock"
                )
