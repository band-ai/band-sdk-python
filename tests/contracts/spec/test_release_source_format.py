"""P1/N-9: current release formats with a frontmatter default."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

from tests.paths import CONTRACTS_ROOT as ROOT


class TestReleaseSourceFormat(unittest.TestCase):
    def invoke(
        self, *args: str, pinned: bool = False
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            cli = path / "contract-engine"
            project = "engines/band-v1/contract-lab" if pinned else "contract-lab"
            shutil.copy2(ROOT / project / "rel/overlays/bin/contract-engine", cli)
            backend = path / "contract_engine"
            backend.write_text(
                '#!/bin/sh\nprintf "%s\\n%s\\n" "$BAND_CONTRACT_SOURCE" "$BAND_CONTRACT_FORMAT"\n'
            )
            backend.chmod(0o755)
            return subprocess.run(
                [str(cli), *args], capture_output=True, text=True, check=False
            )

    @pytest.mark.contract_posix
    def test_omitted_format_defaults_to_frontmatter(self) -> None:
        result = self.invoke("contract with spaces.band")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(), ["contract with spaces.band", "frontmatter"]
        )

    @pytest.mark.contract_posix
    def test_explicit_frontmatter_is_supported(self) -> None:
        result = self.invoke("contract.band", "frontmatter")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["contract.band", "frontmatter"])

    @pytest.mark.contract_posix
    def test_explicit_standard_is_supported(self) -> None:
        result = self.invoke("contract with spaces.band", "standard")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(), ["contract with spaces.band", "standard"]
        )

    @pytest.mark.contract_posix
    def test_file_source_host_format_is_supported_in_both_release_launchers(
        self,
    ) -> None:
        for pinned in (False, True):
            with self.subTest(pinned=pinned):
                result = self.invoke(
                    "private source.json", "file-source", pinned=pinned
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout.splitlines(), ["private source.json", "file-source"]
                )

    @pytest.mark.contract_posix
    def test_bad_format_prints_current_usage_and_exits_two_before_starting_backend(
        self,
    ) -> None:
        for format in ("native", "typo", ""):
            with self.subTest(format=format):
                result = self.invoke("contract.band", format)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(
                    "Usage: contract-engine CONTRACT_PATH [frontmatter|standard|file-source]",
                    result.stderr,
                )
                self.assertNotIn("native", result.stderr)

    @pytest.mark.contract_posix
    def test_wrong_arity_prints_only_current_usage(self) -> None:
        for args in ((), ("contract.band", "frontmatter", "extra")):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(
                    "Usage: contract-engine CONTRACT_PATH [frontmatter|standard|file-source]",
                    result.stderr,
                )
                self.assertNotIn("native", result.stderr)


if __name__ == "__main__":
    unittest.main()
