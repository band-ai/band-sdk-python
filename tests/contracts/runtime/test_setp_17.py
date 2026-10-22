from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from remote_agent.hostkey import HostKey
from remote_agent.storage import StoreError
from remote_agent.store import Store
from tests.contracts.runtime.required import require_value
from tests.contracts.runtime.setp17 import (
    CRASH,
    AtomicInitialKeyFixture,
    CompetingKeyPublication,
)


@pytest.mark.contract_linux
class TestAtomicInitialKey(AtomicInitialKeyFixture):
    def test_killed_partial_first_write_is_absent_and_restart_can_create_key(
        self,
    ) -> None:
        result = subprocess.run(
            [sys.executable, "-c", CRASH, str(self.path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 91, result.stderr)
        self.assertFalse(
            Path(str(self.path) + ".host-key").exists(),
            "Partial first key was published",
        )
        with Store(self.path) as store:
            host = HostKey(store)
            host.ensure()
            self.assertEqual(
                len(require_value(host.read(), "Expected the persisted host key")), 32
            )
            self.assertEqual(host.path.stat().st_nlink, 1)
            self.assertFalse(
                list(self.root.glob(".host-key-create-*")),
                "Restart retained a staged key",
            )

    def test_existing_key_racing_publication_is_never_overwritten(self) -> None:
        with Store(self.path) as store:
            host = HostKey(store)
            race = CompetingKeyPublication(host.path)
            with (
                patch("remote_agent.hostkey.os.fsync", side_effect=race),
                self.assertRaises(StoreError),
            ):
                host.create(b"n" * 32)
            self.assertEqual(host.path.read_bytes(), b"r" * 32)
            self.assertEqual(host.path.stat().st_nlink, 1)
            self.assertFalse(list(self.root.glob(".host-key-create-*")))
