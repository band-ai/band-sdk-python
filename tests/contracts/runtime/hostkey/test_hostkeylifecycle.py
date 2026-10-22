from __future__ import annotations

import base64
import os
import secrets
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

import remote_agent
from remote_agent.commands import capture
from remote_agent.hostkey import HostKey
from remote_agent.program import Artifact, Program
from remote_agent.protocol import decode
from remote_agent.storage import StoreError, canonical
from remote_agent.store import Store
from tests.contracts.runtime.hostkey.kit import HostKeyLifecycleFixture
from tests.contracts.runtime.sandbox import PREAMBLE


class TestHostKeyLifecycle(HostKeyLifecycleFixture):
    @pytest.mark.contract_linux
    def test_storage_is_private_and_journal_contains_only_a_short_fingerprint(
        self,
    ) -> None:
        host = HostKey(self.store)
        with patch(
            "remote_agent.hostkey.secrets.token_bytes", wraps=secrets.token_bytes
        ) as random:
            host.ensure()
            host.ensure()
        random.assert_called_once_with(32)
        key = host.path.read_bytes()
        self.assertTrue(len(key) == 32)
        self.assertEqual(host.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(key not in canonical(self.store.records()))
        self.assertTrue(key.hex().encode() not in canonical(self.store.records()))
        self.assertEqual(self.store.records()[0]["kind"], "host_key_created")
        self.assertEqual(set(self.store.records()[0]["data"]), {"key_id"})
        self.assertEqual(len(self.store.records()[0]["data"]["key_id"]), 8)

    @pytest.mark.contract_linux
    def test_restart_reuses_key_and_two_hosts_mint_distinct_ids_without_payload(
        self,
    ) -> None:
        host = HostKey(self.store)
        first = host.identifier("session", "actor", "input", 1)
        self.store.close()
        self.store = Store(self.root / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.assertEqual(
            first, HostKey(self.store).identifier("session", "actor", "input", 1)
        )
        with Store(self.root / "other.sqlite3") as other:
            self.assertNotEqual(
                first, HostKey(other).identifier("session", "actor", "input", 1)
            )
        self.assertEqual(len(first), 47)

    @pytest.mark.contract_linux
    def test_lost_key_is_replaced_and_recovery_is_fingerprint_only(self) -> None:
        host = HostKey(self.store)
        before = host.identifier("s", "a", "x", 1)
        host.path.unlink()
        after = host.identifier("s", "a", "x", 1)
        self.assertNotEqual(before, after)
        self.assertEqual(self.store.records()[-1]["kind"], "host_key_recovered")
        self.assertEqual(set(self.store.records()[-1]["data"]), {"key_id"})

    @pytest.mark.contract_linux
    def test_symlink_hardlink_wrong_mode_and_bad_size_are_refused(self) -> None:
        host = HostKey(self.store)
        target = self.root / "untouched"
        target.write_text("not a key")
        target.chmod(0o600)
        host.path.symlink_to(target)
        with self.assertRaises(StoreError):
            host.ensure()
        self.assertEqual(target.read_text(), "not a key")
        host.path.unlink()
        host.ensure()
        os.link(host.path, self.root / "alias")
        with self.assertRaises(StoreError):
            host.ensure()
        (self.root / "alias").unlink()
        host.path.chmod(0o644)
        with self.assertRaises(StoreError):
            host.ensure()
        host.path.chmod(0o600)
        host.path.write_bytes(b"bad")
        with self.assertRaises(StoreError):
            host.ensure()

    @pytest.mark.contract_linux
    def test_bind_visible_key_paths_are_rejected_before_any_creation(self) -> None:
        package = Path(remote_agent.__file__).resolve().parent
        for directory in (Path("/usr"), Path("/lib"), Path("/lib64"), package):
            with (
                self.subTest(directory=str(directory)),
                self.assertRaises(StoreError),
            ):
                HostKey(SimpleNamespace(path=directory / "unsafe.sqlite3"))

    @pytest.mark.contract_linux
    def test_artifact_cannot_equal_the_private_key_or_resolve_to_it(self) -> None:
        host = HostKey(self.store)
        host.ensure()
        alias = self.root / "artifact-alias"
        alias.symlink_to(host.path)
        for path in (host.path, alias):
            with self.assertRaises(StoreError):
                host.assert_hidden(cast(Artifact, SimpleNamespace(path=path)))

    @pytest.mark.contract_isolation
    def test_callback_sees_neither_key_path_nor_inheritable_key_descriptor(
        self,
    ) -> None:
        host = HostKey(self.store)
        host.ensure()
        descriptor = os.open(host.path, os.O_RDONLY)
        try:
            os.set_inheritable(
                descriptor, True
            )  # Deliberately hostile ambient descriptor.
            probe = (
                'import os,json;print(json.dumps({"visible":os.path.exists("/proc/self/fd/'
                + str(descriptor)
                + '")}))'
            )
            result = capture([sys.executable, "-c", probe], {})
            self.assertFalse(decode(base64.b64decode(result["stdout"]))["visible"])
            source = (
                PREAMBLE
                + '\ndef handle(event,state,context):\n    from pathlib import Path\n    return Handled({"visible":Path('
                + repr(str(host.path))
                + ").exists()})\n"
            )
            result = Program(Artifact.publish(source, self.root / "artifacts")).handle(
                {}, {}, {}
            )
            self.assertFalse(result["state"]["visible"])
        finally:
            os.close(descriptor)

    @pytest.mark.contract_linux
    def test_concurrent_first_starts_publish_one_complete_key(self) -> None:
        worker = """from types import SimpleNamespace
from remote_agent.hostkey import HostKey
import sys
store=SimpleNamespace(path=sys.argv[1],get=lambda _:None,commit=lambda *a,**k:None)
print(HostKey(store).identifier("s","a","x",1))
"""
        path = self.root / "concurrent.sqlite3"
        children = [
            subprocess.Popen(
                [sys.executable, "-c", worker, str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(6)
        ]
        try:
            outputs = []
            for child in children:
                output, error = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0, error.decode())
                outputs.append(output)
            self.assertTrue(
                len(set(outputs)) == 1, "First-start race produced inconsistent IDs"
            )
            self.assertEqual(Path(str(path) + ".host-key").stat().st_size, 32)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate()
