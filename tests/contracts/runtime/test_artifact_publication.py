from __future__ import annotations

import hashlib
import os
import selectors
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from remote_agent.operations import export
from remote_agent.program import Artifact, ProgramError
from remote_agent.storage import StoreError
from remote_agent.store import Store
from tests.contracts.runtime.artifactpublication import (
    ArtifactPublicationFixture,
    start_generic,
)
from tests.contracts.runtime.bandrolequality import SOURCE


class TestArtifactPublication(ArtifactPublicationFixture):
    @pytest.mark.contract_linux
    def test_partial_write_never_publishes_the_final_artifact_name(self) -> None:
        script = """
import os,sys
from remote_agent.program import Artifact
original_fdopen=os.fdopen
def interrupted(descriptor,*args,**kwargs):
    with original_fdopen(descriptor,*args,**kwargs) as stream:
        stream.write(b"qualified")
        stream.flush()
        os.fsync(stream.fileno())
        os._exit(73)
os.fdopen=interrupted
Artifact.publish("qualified source",sys.argv[1])
os._exit(74)
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.child(script, root)
            self.assertEqual(result.returncode, 73, result.stderr)
            final = root / (hashlib.sha256(b"qualified source").hexdigest() + ".py")
            observed = final.read_bytes() if final.exists() else None
            error = None
            artifact: Artifact | None = None
            try:
                artifact = Artifact.publish("qualified source", root)
            except ProgramError as failure:
                error = str(failure)
            self.assertIsNone(
                error,
                f"Exact retry refused after process death: {error}; final={observed!r}",
            )
            self.assertIsNone(
                observed, "Partial bytes cannot acquire the immutable final name"
            )
            assert artifact is not None, "The exact retry must publish an artifact"
            artifact.verify()
            self.assertEqual(final.read_bytes(), b"qualified source")
            self.assertEqual(Artifact.publish("qualified source", root), artifact)

    @pytest.mark.contract_isolation
    def test_generic_startup_retries_the_real_qualification_before_immutable_pins(
        self,
    ) -> None:
        script = """
import os,sys
from pathlib import Path
from tests.contracts.runtime.artifactpublication import start_generic
original_fdopen=os.fdopen
def interrupted(descriptor,*args,**kwargs):
    path=Path(os.readlink("/proc/self/fd/"+str(descriptor)))
    if path.parent.name=="artifacts":
        with original_fdopen(descriptor,*args,**kwargs) as stream:
            stream.write(b"from remote_agent.sdk ")
            stream.flush()
            os.fsync(stream.fileno())
            os._exit(73)
    return original_fdopen(descriptor,*args,**kwargs)
os.fdopen=interrupted
start_generic(Path(sys.argv[1]))
os._exit(74)
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.child(script, root)
            self.assertEqual(result.returncode, 73, result.stderr)
            self.assertFalse((root / "runtime" / "initial.py").exists())
            artifacts = root / "runtime" / "initial-validation" / "artifacts"
            final = artifacts / (hashlib.sha256(SOURCE.encode()).hexdigest() + ".py")
            observed = final.read_bytes() if final.exists() else None
            error = None
            outcome: str | None = None
            try:
                outcome = start_generic(root)
            except ProgramError as failure:
                error = str(failure)
            self.assertIsNone(
                error, f"Qualification remained poisoned: {error}; final={observed!r}"
            )
            self.assertEqual(outcome, "qualified")
            self.assertIsNone(observed)
            self.assertEqual(final.read_text(), SOURCE)
            self.assertEqual((root / "runtime" / "initial.py").read_text(), SOURCE)
            self.assertEqual(start_generic(root), "qualified")

    @pytest.mark.contract_linux
    def test_interruption_after_final_link_retries_without_weakening_alias_checks(
        self,
    ) -> None:
        script = """
import os,sys
from remote_agent.program import Artifact
original_link=os.link
def interrupted(*args,**kwargs):
    original_link(*args,**kwargs)
    os._exit(73)
os.link=interrupted
Artifact.publish("qualified source",sys.argv[1])
os._exit(74)
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.child(script, root)
            self.assertEqual(result.returncode, 73, result.stderr)
            final = root / (hashlib.sha256(b"qualified source").hexdigest() + ".py")
            self.assertEqual(final.stat().st_nlink, 2)
            unrelated = root / ".journal-export-unrelated"
            unrelated.write_bytes(b"another publication")
            artifact = Artifact.publish("qualified source", root)
            artifact.verify()
            self.assertEqual(final.stat().st_nlink, 1)
            self.assertEqual(unrelated.read_bytes(), b"another publication")
            os.link(final, root / "unrelated-alias")
            with self.assertRaises(ProgramError):
                Artifact.publish("qualified source", root)
            self.assertEqual(final.stat().st_nlink, 2)

    @pytest.mark.contract_linux
    def test_existing_different_bytes_and_symlinks_are_refused_without_rewrite(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = Artifact.publish("qualified source", root)
            final = Path(artifact.path)
            final.write_bytes(b"changed")
            with self.assertRaisesRegex(ProgramError, "hash mismatch"):
                Artifact.publish("qualified source", root)
            self.assertEqual(final.read_bytes(), b"changed")
            final.unlink()
            other = root / "other.py"
            other.write_bytes(b"qualified source")
            final.symlink_to(other)
            with self.assertRaises(ProgramError):
                Artifact.publish("qualified source", root)
            self.assertTrue(final.is_symlink())
            self.assertEqual(other.read_bytes(), b"qualified source")

    @pytest.mark.contract_linux
    def test_concurrent_identical_publication_survives_staging_recovery(self) -> None:
        script = """
import os,sys
from remote_agent.program import Artifact
original_link=os.link
def pause_after_link(*args,**kwargs):
    original_link(*args,**kwargs)
    print("linked",flush=True)
    sys.stdin.readline()
os.link=pause_after_link
Artifact.publish("qualified source",sys.argv[1]).verify()
"""
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                [sys.executable, "-B", "-W", "error", "-c", script, directory],
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert process.stdout is not None, (
                "The publisher must have a captured output pipe"
            )
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(
                        selector.select(timeout=5),
                        "Publisher did not reach the complete link",
                    )
                self.assertEqual(process.stdout.readline().strip(), "linked")
                artifact = Artifact.publish("qualified source", directory)
                artifact.verify()
                _, errors = process.communicate("continue\n", timeout=5)
                self.assertEqual(process.returncode, 0, errors)
                self.assertEqual(Path(artifact.path).stat().st_nlink, 1)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)

    @pytest.mark.contract_linux
    def test_existing_operator_export_stays_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with Store(root / "journal.sqlite3") as store:
                store.commit("first", {})
                destination = root / "export.jsonl"
                export(store.path, destination)
                original = destination.read_bytes()
                store.commit("second", {})
                with self.assertRaises(StoreError):
                    export(store.path, destination)
                self.assertEqual(destination.read_bytes(), original)
