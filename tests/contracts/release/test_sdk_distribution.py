"""Require the canonical SDK artifact while accepting sealed dependencies."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from contracts.deploy.releaseverification import verify_release
from tests.contracts.release.kit import release, seal, wheel


class TestSdkDistribution(TestCase):
    def test_canonical_sdk_is_the_installable_wheel(self) -> None:
        with TemporaryDirectory() as directory:
            root = release(Path(directory) / "release")
            self.assertEqual(
                verify_release(root).wheel.name, "band_sdk-6.0.1-py3-none-any.whl"
            )

    def test_standalone_contract_package_cannot_replace_the_sdk(self) -> None:
        with TemporaryDirectory() as directory:
            root = release(Path(directory) / "release", "contract-remote-agent")
            with self.assertRaisesRegex(ValueError, "band-sdk"):
                verify_release(root)

    def test_sdk_filename_cannot_hide_another_distribution(self) -> None:
        with TemporaryDirectory() as directory:
            root = release(Path(directory) / "release")
            wheel(
                root / "wheels/band_sdk-6.0.1-py3-none-any.whl",
                "contract-remote-agent",
            )
            seal(root)
            with self.assertRaisesRegex(ValueError, "band-sdk"):
                verify_release(root)

    def test_sealed_dependency_wheel_does_not_compete_with_the_sdk(self) -> None:
        with TemporaryDirectory() as directory:
            root = release(Path(directory) / "release")
            dependency = wheel(
                root / "dependencies/pydantic-6.0.1-py3-none-any.whl", "pydantic"
            )
            seal(root)
            verified = verify_release(root)
            self.assertEqual(verified.wheel.parent, root / "wheels")
            self.assertIn(
                dependency.relative_to(root).as_posix(), verified.manifest["files"]
            )

    def test_unsealed_dependency_is_rejected_before_installation(self) -> None:
        with TemporaryDirectory() as directory:
            root = release(Path(directory) / "release")
            wheel(root / "dependencies/extra-6.0.1-py3-none-any.whl", "extra")
            with self.assertRaisesRegex(ValueError, "inventory"):
                verify_release(root)
