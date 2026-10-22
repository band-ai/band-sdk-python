"""Actually run the default write path in an isolated copy and protect pinned evidence."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from contracts.spec.tools import generatevectors
from tests.contracts.spec.workspace import link_or_copy


class TestDefaultWrite(unittest.TestCase):
    @pytest.mark.contract_posix
    @pytest.mark.contract_engine
    def test_unprefixed_write_retains_pinned_fixtures_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(
                generatevectors.ROOT / "spec",
                root / "spec",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            link_or_copy(generatevectors.ROOT / "contract-lab", root / "contract-lab")
            (root / "agent-runtime/evidence").mkdir(parents=True)
            bundle = root / "spec/conformance"
            before = json.loads((bundle / "manifest.json").read_text())
            frozen = {
                entry["path"]: (bundle / entry["path"]).read_bytes()
                for entry in before["cases"] + before["artifacts"]
            }
            with (
                patch.object(generatevectors, "ROOT", root),
                patch("sys.argv", ["generatevectors", "--write"]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(generatevectors.main(), 0)
            after = json.loads((bundle / "manifest.json").read_text())
            for kind in ("cases", "artifacts"):
                for entry in before[kind]:
                    self.assertIn(entry, after[kind])
            for name, value in frozen.items():
                self.assertEqual((bundle / name).read_bytes(), value, name)


if __name__ == "__main__":
    unittest.main()
