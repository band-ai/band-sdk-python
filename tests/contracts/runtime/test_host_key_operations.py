from __future__ import annotations

import base64
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from remote_agent import operations
from remote_agent.hostkey import HostKey
from remote_agent.storage import StoreError, canonical
from remote_agent.store import Store
from tests.contracts.runtime.hostkeyoperations import HostKeyOperationsFixture
from tests.contracts.runtime.required import require_value


class TestHostKeyOperations(HostKeyOperationsFixture):
    @pytest.mark.contract_linux
    def test_export_excludes_key_encodings_while_live_backup_restores_same_key(
        self,
    ) -> None:
        export, backup = self.root / "journal.jsonl", self.root / "backup.sqlite3"
        key = self.host.path.read_bytes()
        operations.export(self.store.path, export)
        for encoded in (key, key.hex().encode(), base64.b64encode(key)):
            self.assertTrue(
                encoded not in export.read_bytes(), "Journal export disclosed a key"
            )
        operations.backup(self.store.path, backup)
        with sqlite3.connect(backup) as db:
            tables = {
                r[0]
                for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertIn(
                "private_host_key", tables, "K4: credential backup omitted host key"
            )
            saved = db.execute(
                "SELECT secret FROM private_host_key WHERE singleton=1"
            ).fetchone()[0]
            self.assertTrue(saved == key, "Backup did not preserve the exact host key")
        restored = self.root / "restore/state.sqlite3"
        operations.restore(backup, restored)
        with Store(restored) as store:
            self.assertTrue(
                HostKey(store).path.read_bytes() == key, "Restore lost the key"
            )
            self.assertEqual(
                HostKey(store).identifier("s", "a", "x", 1),
                self.host.identifier("s", "a", "x", 1),
            )
            self.assertFalse(
                require_value(store.db, "Expected a durable SQLite connection")
                .execute("SELECT 1 FROM sqlite_master WHERE name='private_host_key'")
                .fetchone()
            )
            self.assertTrue(
                key not in restored.read_bytes(),
                "Live DB retained backup credential bytes",
            )
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    @pytest.mark.contract_linux
    def test_store_backup_also_includes_key_and_legacy_backup_recovers_missing_key(
        self,
    ) -> None:
        backup = self.root / "direct.sqlite3"
        self.store.backup(backup)
        restored = self.root / "direct-restore.sqlite3"
        Store.restore(backup, restored)
        with Store(restored) as store:
            self.assertTrue(HostKey(store).path.exists(), "Store.backup omitted key")
        with sqlite3.connect(backup) as db:
            db.execute("DROP TABLE private_host_key")
        missing = self.root / "legacy-restore.sqlite3"
        Store.restore(backup, missing)
        with Store(missing) as store:
            host = HostKey(store)
            host.ensure()
            self.assertEqual(store.records()[-1]["kind"], "host_key_recovered")
            self.assertNotEqual(
                host.identifier("s", "a", "x", 1),
                self.host.identifier("s", "a", "x", 1),
            )

    @pytest.mark.contract_linux
    def test_restore_refuses_existing_destination_key_without_overwriting_it(
        self,
    ) -> None:
        backup = self.root / "backup.sqlite3"
        operations.backup(self.store.path, backup)
        restored = self.root / "restore.sqlite3"
        key_path = Path(str(restored) + ".host-key")
        key_path.write_bytes(b"preserve")
        key_path.chmod(0o600)
        with self.assertRaises(StoreError):
            operations.restore(backup, restored)
        self.assertEqual(key_path.read_bytes(), b"preserve")
        self.assertFalse(restored.exists())

    @pytest.mark.contract_linux
    def test_malformed_backup_key_is_rejected_before_publishing_any_destination(
        self,
    ) -> None:
        backup = self.root / "bad.sqlite3"
        operations.backup(self.store.path, backup)
        with sqlite3.connect(backup) as db:
            db.execute("DROP TABLE IF EXISTS private_host_key")
            db.execute("CREATE TABLE private_host_key(singleton INTEGER, secret BLOB)")
            db.execute("INSERT INTO private_host_key VALUES(1, X'00')")
        restored = self.root / "invalid.sqlite3"
        with self.assertRaises(StoreError):
            operations.restore(backup, restored)
        self.assertFalse(restored.exists())
        self.assertFalse(Path(str(restored) + ".host-key").exists())

    @pytest.mark.contract_linux
    def test_restore_never_removes_a_key_created_by_a_racing_operator(self) -> None:
        backup = self.root / "race-backup.sqlite3"
        operations.backup(self.store.path, backup)
        restored = self.root / "race-restore.sqlite3"
        key_path = Path(str(restored) + ".host-key")
        original = HostKey.create

        def race(host: Any, key: Any) -> Any:
            host.path.write_bytes(b"racing-owner-must-be-preserved")
            host.path.chmod(0o600)
            return original(host, key)

        with (
            patch.object(HostKey, "create", race),
            self.assertRaises(StoreError),
        ):
            operations.restore(backup, restored)
        self.assertTrue(
            key_path.exists(), "Failed exclusive creation removed another owner key"
        )
        self.assertEqual(key_path.read_bytes(), b"racing-owner-must-be-preserved")
        self.assertFalse(restored.exists())

    @pytest.mark.contract_runtime
    def test_operator_rotation_is_live_and_never_prints_or_journals_key_material(
        self,
    ) -> None:
        before = self.host.path.read_bytes()
        old_id = self.host.identifier("s", "a", "x", 1)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "remote_agent",
                "rotate-host-key",
                str(self.store.path),
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(
            result.returncode, 0, "Operator rotation command unavailable or refused"
        )
        after = self.host.path.read_bytes()
        self.assertTrue(before != after, "Rotation reused the old key")
        self.assertEqual(set(json.loads(result.stdout)), {"event", "key_id"})
        for key in (before, after):
            for encoded in (key, key.hex().encode(), base64.b64encode(key)):
                self.assertTrue(
                    encoded not in result.stdout + result.stderr, "CLI disclosed a key"
                )
                self.assertTrue(
                    encoded not in canonical(self.store.records()),
                    "Journal disclosed a key",
                )
        self.assertNotEqual(old_id, self.host.identifier("s", "a", "x", 1))
        self.assertEqual(self.store.records()[-1]["kind"], "host_key_rotated")
        self.assertEqual(self.host.path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.root.glob(".host-key-rotate-*")))

    @pytest.mark.contract_linux
    def test_failed_rename_keeps_old_key_and_discards_the_temporary_key(self) -> None:
        before = self.host.path.read_bytes()
        with (
            patch(
                "remote_agent.hostkey.os.replace",
                side_effect=OSError("injected rename failure"),
            ),
            self.assertRaises(StoreError),
        ):
            self.host.rotate()
        self.assertTrue(
            self.host.path.read_bytes() == before, "Failed rename lost the old key"
        )
        self.assertFalse(list(self.root.glob(".host-key-rotate-*")))
        self.host.ensure()
        self.assertEqual(
            self.store.records()[-1]["kind"], "host_key_rotation_abandoned"
        )
        self.assertIsNone(self.store.get("host_key_rotation"))

    @pytest.mark.contract_linux
    def test_crash_after_rename_recovers_rotation_without_another_key_change(
        self,
    ) -> None:
        commit = self.store.commit

        def fail_completion(kind: Any, *args: Any, **kwargs: Any) -> Any:
            if kind == "host_key_rotated":
                raise StoreError("injected completion failure")
            return commit(kind, *args, **kwargs)

        with (
            patch.object(self.store, "commit", fail_completion),
            self.assertRaises(StoreError),
        ):
            self.host.rotate()
        after = self.host.path.read_bytes()
        self.host.ensure()
        self.assertTrue(self.host.path.read_bytes() == after, "Recovery rotated again")
        self.assertEqual(self.store.records()[-1]["kind"], "host_key_rotated")
        self.assertIsNone(self.store.get("host_key_rotation"))
