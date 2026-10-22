"""M21: unsealed artifacts never reach installation, including optimized Python."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from contracts.deploy import installation
from contracts.deploy.releaseverification import verify_release
from tests.contracts.release import kit
from tests.contracts.release.entrypoints import arguments, call_entrypoint
from tests.paths import CONTRACT_DEPLOY_ROOT, REPO_ROOT


class TestReleaseVerification(unittest.TestCase):
    def test_valid_inventory_returns_the_manifest_wheel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = kit.release(Path(directory) / "release")
            verified = verify_release(root)
            self.assertEqual(
                verified.wheel, root / "wheels/band_sdk-6.0.1-py3-none-any.whl"
            )
            self.assertEqual(
                set(verified.manifest["files"]),
                {
                    "wheels/band_sdk-6.0.1-py3-none-any.whl",
                    "engine/bin/contract-engine",
                    "requirements-runtime.lock",
                },
            )

    def test_smoke_installs_only_the_verified_wheel(self) -> None:
        module = importlib.import_module("contracts.deploy.smokerelease")
        with tempfile.TemporaryDirectory() as directory:
            root = kit.release(Path(directory) / "release")
            with (
                patch.object(sys, "argv", ["smokerelease", str(root)]),
                patch("venv.create"),
                patch.object(installation.subprocess, "run", return_value=None) as run,
                patch.object(
                    module,
                    "run_qualification",
                    side_effect=RuntimeError("probe reached"),
                ),
                self.assertRaisesRegex(RuntimeError, "probe reached"),
            ):
                module.main()
            self.assertEqual(
                Path(run.call_args_list[1].args[0][-1]).name,
                "band_sdk-6.0.1-py3-none-any.whl",
            )

    def test_missing_file_bad_modes_dirty_source_and_unsafe_paths_fail(self) -> None:
        for mutation in ("missing", "dirty", "path"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = kit.release(Path(directory) / "release")
                manifest = json.loads((root / "manifest.json").read_text())
                if mutation == "missing":
                    (root / "engine/bin/contract-engine").unlink()
                elif mutation == "dirty":
                    manifest["source_dirty"] = True
                elif mutation == "path":
                    manifest["files"]["../outside"] = "0" * 64
                    manifest["modes"]["../outside"] = 0o644
                (root / "manifest.json").write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    verify_release(root)

    @pytest.mark.contract_posix
    def test_changed_executable_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = kit.release(Path(directory) / "release")
            (root / "engine/bin/contract-engine").chmod(0o644)
            with self.assertRaisesRegex(ValueError, "permission mismatch"):
                verify_release(root)

    @pytest.mark.contract_posix
    def test_directory_and_root_symlinks_are_rejected(self) -> None:
        for mutation in ("directory-link", "root-link"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = kit.release(Path(directory) / "release")
                if mutation == "directory-link":
                    (root / "unused").symlink_to(
                        Path(directory), target_is_directory=True
                    )
                else:
                    alias = Path(directory) / "alias"
                    alias.symlink_to(root, target_is_directory=True)
                    root = alias
                with self.assertRaisesRegex(ValueError, "symlink"):
                    verify_release(root)

    def test_isolated_entrypoints_load_helpers_and_reject_inventory(self) -> None:
        for name in (
            "smokerelease",
            "qualifylifecycle",
            "qualifyband",
            "qualifysystemd",
        ):
            with (
                self.subTest(entrypoint=name),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = kit.release(Path(directory) / "release")
                (root / "unexpected").write_text("not sealed")
                state = Path(directory) / "state"
                argv = arguments(name, root, state)
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(CONTRACT_DEPLOY_ROOT / (name + ".py")),
                        *argv,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("inventory mismatch", result.stderr)
                self.assertNotIn("ModuleNotFoundError", result.stderr)
                self.assertFalse(state.exists())

    def test_all_entrypoints_reject_unlisted_wheel_before_side_effects(self) -> None:
        for name in (
            "smokerelease",
            "qualifylifecycle",
            "qualifyband",
            "qualifysystemd",
        ):
            with (
                self.subTest(entrypoint=name),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = kit.release(Path(directory) / "release")
                (root / "wheels/a_unlisted-9-py3-none-any.whl").write_bytes(b"unsealed")
                with self.assertRaisesRegex(ValueError, "inventory"):
                    call_entrypoint(name, root, Path(directory) / "state")

    @pytest.mark.contract_posix
    def test_symlink_with_matching_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = kit.release(Path(directory) / "release")
            engine = root / "engine/bin/contract-engine"
            outside = Path(directory) / "external-engine"
            engine.rename(outside)
            engine.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symlink"):
                call_entrypoint("smokerelease", root, Path(directory) / "state")

    def test_multiple_manifest_wheels_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = kit.release(Path(directory) / "release")
            (root / "wheels/second-1-py3-none-any.whl").write_bytes(b"other")
            kit.seal(root)
            with self.assertRaisesRegex(ValueError, "one wheel"):
                call_entrypoint("smokerelease", root, Path(directory) / "state")

    def test_optimized_entrypoints_reject_modified_bytes(self) -> None:
        for name in ("qualifyband", "qualifylifecycle", "qualifysystemd"):
            with (
                self.subTest(entrypoint=name),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = kit.release(Path(directory) / "release")
                (root / "engine/bin/contract-engine").write_bytes(b"altered")
                code = (
                    "import sys;sys.path.insert(0," + repr(str(REPO_ROOT)) + ");"
                    "from tests.contracts.release.entrypoints import call_entrypoint;"
                    "from pathlib import Path;"
                    "call_entrypoint("
                    + repr(name)
                    + ",Path("
                    + repr(str(root))
                    + "),Path("
                    + repr(str(Path(directory) / "state"))
                    + "))"
                )
                result = subprocess.run(
                    [sys.executable, "-O", "-c", code],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Release content mismatch", result.stderr)
                self.assertNotIn("unsafe ", result.stderr)
