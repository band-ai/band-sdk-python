"""Exercise dependency installation through the actual release smoke entrypoint."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import pytest

from contracts.deploy.smokerelease import main
from tests.contracts.release.kit import release, seal, wheel


@pytest.mark.contract_linux
class TestOfflineInstallation(TestCase):
    def test_smoke_installs_locked_dependencies_from_the_sealed_release(self) -> None:
        with TemporaryDirectory() as directory:
            root = release(Path(directory) / "release")
            dependency = wheel(
                root / "dependencies/contract_probe-6.0.1-py3-none-any.whl",
                "contract-probe",
                modules={"contract_probe.py": "VALUE = 'sealed local dependency'\n"},
            )
            wheel(
                root / "wheels/band_sdk-6.0.1-py3-none-any.whl",
                requirements=("contract-probe==6.0.1",),
                modules={
                    "band_probe.py": "from contract_probe import VALUE\n"
                    "assert VALUE == 'sealed local dependency'\n"
                },
            )
            digest = hashlib.sha256(dependency.read_bytes()).hexdigest()
            (root / "requirements-runtime.lock").write_text(
                f"contract-probe==6.0.1 --hash=sha256:{digest}\n", encoding="utf-8"
            )
            seal(root)
            with (
                patch("sys.argv", ["smoke-release", str(root)]),
                patch("contracts.deploy.smokerelease.PROBE", "import band_probe\n"),
                patch(
                    "contracts.deploy.smokerelease.run_qualification",
                    side_effect=subprocess.run,
                ),
            ):
                main()
