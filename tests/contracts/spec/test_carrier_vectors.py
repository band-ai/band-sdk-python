"""Protect pinned legacy evidence and selected-run reporting at the QA boundary."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from contracts.spec.tools import checkspec, generatevectors
from tests.contracts.spec.workspace import link_or_copy


class TestCarrierSelection(unittest.TestCase):
    def test_selected_carriers_do_not_parse_legacy_contracts(self) -> None:
        with patch.object(
            generatevectors,
            "source",
            side_effect=AssertionError("legacy source evaluated"),
        ):
            selected = generatevectors.cases("carrier-")
        self.assertGreater(len(selected), 30)
        self.assertTrue(all(case["id"].startswith("carrier-") for case in selected))

    def generate(self, root: Path, observed: dict[str, Any]) -> int:
        case = {
            "profile": "band-contract/conformance-case/1",
            "id": "carrier-probe",
            "requirements": ["ENG-002"],
            "requests": [{"op": "wire", "bytes": "{}"}],
            "assertions": [{"response": 0, "pointer": "/error", "equals": "envelope"}],
        }
        with (
            patch.object(generatevectors, "ROOT", root),
            patch.object(generatevectors, "cases", return_value=[case]),
            patch.object(generatevectors, "adapter", return_value=[observed]),
            patch(
                "sys.argv", ["generatevectors", "--write", "--case-prefix", "carrier-"]
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return generatevectors.main()

    def temp_bundle(self, directory: str | Path) -> tuple[Path, Path, dict[str, Any]]:
        root = Path(directory)
        bundle = root / "spec/conformance"
        bundle.mkdir(parents=True)
        (root / "agent-runtime/evidence").mkdir(parents=True)
        (root / "spec/requirements.json").write_bytes(
            (generatevectors.ROOT / "spec/requirements.json").read_bytes()
        )
        legacy = {
            "id": "legacy",
            "path": "case-legacy.json",
            "sha256": "old",
            "requests": 1,
            "requirements": ["ENG-002"],
        }
        manifest = {
            "profile": "band-contract/conformance/1",
            "revision": "unchanged",
            "provenance": "unchanged",
            "cases": [legacy],
            "artifacts": [
                {
                    "path": "legacy.bin",
                    "schema": "opaque",
                    "sha256": "old",
                    "requirements": ["ENG-002"],
                }
            ],
        }
        (bundle / "manifest.json").write_text(json.dumps(manifest))
        (bundle / "case-legacy.json").write_bytes(b"legacy fixture: never regenerate\n")
        (bundle / "legacy.bin").write_bytes(b"legacy artifact\n")
        return root, bundle, manifest

    def test_selected_write_retains_other_pins_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, bundle, before = self.temp_bundle(directory)
            self.assertEqual(self.generate(root, {"error": "envelope"}), 0)
            after = json.loads((bundle / "manifest.json").read_text())
            self.assertEqual(after["cases"][:1], before["cases"])
            self.assertEqual(after["artifacts"], before["artifacts"])
            self.assertEqual(after["provenance"], before["provenance"])
            self.assertEqual(
                (bundle / "case-legacy.json").read_bytes(),
                b"legacy fixture: never regenerate\n",
            )
            self.assertEqual((bundle / "legacy.bin").read_bytes(), b"legacy artifact\n")
            self.assertEqual(
                [row["id"] for row in after["cases"]], ["legacy", "carrier-probe"]
            )

    def test_failed_assertion_writes_no_fixture_or_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, bundle, _ = self.temp_bundle(directory)
            before = {path.name: path.read_bytes() for path in bundle.iterdir()}
            self.assertEqual(self.generate(root, {"ok": {}}), 1)
            self.assertEqual(
                {path.name: path.read_bytes() for path in bundle.iterdir()}, before
            )

    @pytest.mark.contract_linux
    def test_partial_checker_compares_selected_outputs_and_labels_scope(self) -> None:
        manifest = json.loads(
            (checkspec.ROOT / "spec/conformance/manifest.json").read_text()
        )
        rows = [row for row in manifest["cases"] if row["id"].startswith("carrier-")]
        self.assertTrue(rows, "generate the reviewed carrier fixtures first")
        responses = []
        for row in rows:
            responses.extend(
                json.loads(
                    (checkspec.ROOT / "spec/conformance" / row["path"]).read_text()
                )["expected"]
            )
        with (
            patch.object(checkspec, "adapter", return_value=responses) as adapter,
            patch("sys.argv", ["checkspec", "--case-prefix", "carrier-"]),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            checkspec.main()

        report = json.loads(output.getvalue())
        self.assertEqual(report["adapter_scope"], "carrier-")
        self.assertEqual(report["adapter_requests"], len(responses))
        self.assertGreater(report["requests"], report["adapter_requests"])
        self.assertEqual(len(adapter.call_args.args[0]), len(responses))
        wrong = copy.deepcopy(responses)
        wrong[0] = {"error": "envelope"}
        with (
            patch.object(checkspec, "adapter", return_value=wrong),
            patch("sys.argv", ["checkspec", "--case-prefix", "carrier-"]),
            self.assertRaisesRegex(
                AssertionError, "differs from its immutable fixture"
            ),
        ):
            checkspec.main()

    def test_successful_wire_step_requires_checkpoint_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(
                checkspec.ROOT / "spec",
                root / "spec",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            for name in (
                "deploy",
                "README.md",
                "PRODUCTION.md",
                "agent-runtime",
                "header-spikes",
                "contract-lab",
            ):
                link_or_copy(checkspec.ROOT / name, root / name)
            path = root / "spec/conformance/case-carrier-cross-carrier-retry.json"
            case = json.loads(path.read_text())
            del case["expected"][2]["state"]
            path.write_text(json.dumps(case, indent=2) + "\n")
            manifest_path = root / "spec/conformance/manifest.json"
            manifest = json.loads(manifest_path.read_text())
            next(row for row in manifest["cases"] if row["path"] == path.name)[
                "sha256"
            ] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            with (
                patch.object(checkspec, "ROOT", root),
                patch("sys.argv", ["checkspec", "--schema-only"]),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(KeyError, "state"),
            ):
                checkspec.main()


if __name__ == "__main__":
    unittest.main()
