from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from remote_agent.program import Artifact
from remote_agent.storage import StoreError
from remote_agent.store import Store
from tests.contracts.runtime.storeops import StoreOperationsFixture


class TestStoreOperations(StoreOperationsFixture):
    @pytest.mark.contract_runtime
    def test_operator_cli_searches_committed_live_history_without_controller_ownership(
        self,
    ) -> None:
        store = self.open()
        store.commit("input", {"text": "matching invoice"})
        store.commit("turn", {"text": "other"})
        store.commit("input", {"text": "matching invoice again"})
        command = [
            sys.executable,
            "-m",
            "remote_agent",
            "journal",
            str(self.path),
            "--query",
            "invoice",
            "--kind",
            "input",
            "--limit",
            "1",
        ]
        first = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        page = json.loads(first.stdout)
        self.assertEqual([r["sequence"] for r in page["records"]], [1])
        store.commit("input", {"text": "matching invoice later"})
        second = subprocess.run(
            command
            + ["--after", str(page["cursor"]), "--through", str(page["through"])],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            [r["sequence"] for r in json.loads(second.stdout)["records"]], [3]
        )
        self.assertEqual(store.journal_head()["sequence"], 4)

    @pytest.mark.contract_linux
    def test_database_lock_wal_and_backup_are_private_even_under_permissive_umask(
        self,
    ) -> None:
        previous = os.umask(0)
        try:
            store = self.open()
            store.commit("private", {"text": "synthetic private state"})
            copy = self.root / "backup.sqlite3"
            store.backup(copy)
        finally:
            os.umask(previous)
        for path in [
            self.path,
            Path(str(self.path) + ".owner"),
            Path(str(self.path) + "-wal"),
            Path(str(self.path) + "-shm"),
            copy,
        ]:
            with self.subTest(file=path.name):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    @pytest.mark.contract_linux
    def test_owned_legacy_database_permissions_are_tightened_without_losing_records(
        self,
    ) -> None:
        store = self.open()
        store.commit("legacy", {}, updates={"state": {"count": 9}})
        store.close()
        self.path.chmod(0o644)
        reopened = self.open()
        self.assertEqual(reopened.get("state"), {"count": 9})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    @pytest.mark.contract_linux
    def test_symlink_database_and_owner_paths_are_rejected_without_touching_target(
        self,
    ) -> None:
        target = self.root / "unrelated"
        target.write_text("preserve this")
        for index, suffix in enumerate(("", ".owner")):
            self.path = self.root / f"linked-{index}.sqlite3"
            path = Path(str(self.path) + suffix)
            with self.subTest(file=path.name):
                path.symlink_to(target)
                with self.assertRaises(StoreError):
                    self.open()
                self.assertEqual(target.read_text(), "preserve this")
                path.unlink()

    @pytest.mark.contract_linux
    def test_generated_artifact_source_is_private(self) -> None:
        previous = os.umask(0)
        try:
            artifact = Artifact.publish(
                "# synthetic confidential program", self.root / "artifacts"
            )
        finally:
            os.umask(previous)
        self.assertEqual(Path(artifact.path).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(artifact.path).parent.stat().st_mode & 0o777, 0o700)

    @pytest.mark.contract_linux
    def test_hard_link_cannot_bypass_single_database_ownership(self) -> None:
        self.open().commit("initial", {})
        alias = self.root / "hardlink.sqlite3"
        os.link(self.path, alias)
        with self.assertRaises(StoreError):
            Store(alias)

    @pytest.mark.contract_linux
    def test_writable_shared_storage_directory_is_refused(self) -> None:
        self.root.chmod(0o777)
        with self.assertRaises(StoreError):
            self.open()

    @pytest.mark.contract_linux
    def test_restore_verifies_complete_history_and_snapshots_before_publishing(
        self,
    ) -> None:
        store = self.open()
        for count in (1, 2):
            store.commit(
                "turn",
                {"count": count},
                updates={"state": {"count": count}},
                snapshot={"program": "p", "schema": "1", "state": {"count": count}},
            )
        backup = self.root / "backup.sqlite3"
        store.backup(backup)
        restored = self.root / "restored.sqlite3"
        Store.restore(backup, restored)
        with Store(restored) as reopened:
            self.assertEqual(reopened.records(), store.records())
            self.assertEqual(reopened.latest_snapshot(), store.latest_snapshot())
        with sqlite3.connect(backup) as database:
            database.execute("UPDATE snapshots SET data='{}' WHERE sequence=1")
        invalid = self.root / "invalid.sqlite3"
        with self.assertRaises(StoreError):
            Store.restore(backup, invalid)
        self.assertFalse(invalid.exists())

    @pytest.mark.contract_linux
    def test_restore_never_overwrites_existing_database(self) -> None:
        store = self.open()
        store.commit("keep", {}, updates={"state": {"count": 7}})
        backup = self.root / "backup.sqlite3"
        store.backup(backup)
        with self.assertRaises(StoreError):
            Store.restore(backup, self.path)
        self.assertEqual(store.get("state"), {"count": 7})

    @pytest.mark.contract_linux
    def test_restore_refuses_real_stale_wal_and_preserves_backup_state(self) -> None:
        store = self.open()
        store.db.execute("PRAGMA wal_autocheckpoint=0")
        for count in range(1, 4):
            store.commit("turn", {"count": count}, updates={"state": {"count": count}})
        backup = self.root / "backup.sqlite3"
        store.backup(backup)
        for count in range(4, 9):
            store.commit("turn", {"count": count}, updates={"state": {"count": count}})
        restored = self.root / "restored.sqlite3"
        stale = {}
        for suffix in ("-wal", "-shm"):
            companion = Path(str(restored) + suffix)
            shutil.copyfile(str(self.path) + suffix, companion)
            stale[companion] = companion.read_bytes()
        with self.assertRaises(StoreError):
            Store.restore(backup, restored)
        self.assertFalse(restored.exists())
        for path, content in stale.items():
            self.assertEqual(path.read_bytes(), content)
        clean = self.root / "clean.sqlite3"
        result = Store.restore(backup, clean)
        self.assertEqual(result["records"], 3)
        with Store(clean) as reopened:
            self.assertEqual(reopened.get("state"), {"count": 3})
            self.assertEqual(len(reopened.records()), 3)

    @pytest.mark.contract_linux
    def test_restore_refuses_each_companion_including_dangling_symlinks(self) -> None:
        store = self.open()
        store.commit("turn", {}, updates={"state": {"count": 1}})
        backup = self.root / "backup.sqlite3"
        store.backup(backup)
        for index, suffix in enumerate(("-wal", "-shm", "-journal")):
            for symlink in (False, True):
                with self.subTest(companion=suffix, symlink=symlink):
                    destination = self.root / f"destination-{index}-{symlink}.sqlite3"
                    companion = Path(str(destination) + suffix)
                    if symlink:
                        companion.symlink_to(self.root / "missing-target")
                    else:
                        companion.write_bytes(b"preserve stale companion")
                    with self.assertRaises(StoreError):
                        Store.restore(backup, destination)
                    self.assertFalse(destination.exists())
                    if symlink:
                        self.assertTrue(companion.is_symlink())
                    else:
                        self.assertEqual(
                            companion.read_bytes(), b"preserve stale companion"
                        )

    @pytest.mark.contract_linux
    def test_missing_backup_does_not_create_a_new_empty_source_or_destination(
        self,
    ) -> None:
        missing, destination = (
            self.root / "missing.sqlite3",
            self.root / "destination.sqlite3",
        )
        with self.assertRaises(StoreError):
            Store.restore(missing, destination)
        self.assertFalse(missing.exists())
        self.assertFalse(destination.exists())
