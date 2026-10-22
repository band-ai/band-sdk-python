"""D-7 release pins: no lost independent assertions or mutable compatibility fixtures."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import pytest

from contracts.spec.tools import generatevectors
from tests.paths import CONTRACTS_ROOT as ROOT


class TestVectorPort(unittest.TestCase):
    @pytest.mark.contract_posix
    @pytest.mark.contract_engine
    def test_generator_uses_only_current_file_sources(self) -> None:
        cases = generatevectors.cases("v2-")
        self.assertTrue(cases, "current semantic replacements are missing")
        for case in cases:
            for request in case["requests"]:
                if "source" in request:
                    self.assertEqual(
                        request["source"].get("profile"), "band-contract/file-source/1"
                    )
                    self.assertNotIn("card", request["source"])

    def test_every_baseline_assertion_has_a_checked_replacement(self) -> None:
        generatevectors.check_parity()

    def test_current_band_case_uses_checked_active_agent_bindings(self) -> None:
        case = generatevectors.cases("v2-band-session")[0]
        config = case["requests"][1]["config"]
        self.assertTrue(
            all(actor.startswith("Agent:") for actor in config["bindings"].values())
        )
        self.assertNotEqual(config["room_policy"].get("mode"), "unchecked")
        for actor in config["bindings"].values():
            principal = config["room_policy"]["principals"][actor]
            self.assertEqual((principal["kind"], principal["active"]), ("agent", True))

    def test_original_baseline_is_not_regenerated(self) -> None:
        baseline = ROOT / "spec/conformance/baseline-v1-pins.json"
        self.assertEqual(
            hashlib.sha256(baseline.read_bytes()).hexdigest(),
            "f2f62ddc8ae231d23da58354f32ccb48ceeb6452d2a109232f95117131e27cb0",
        )

    def test_only_approved_provider_pins_are_superseded(self) -> None:
        original = json.loads(
            (ROOT / "spec/conformance/baseline-v1-pins.json").read_text()
        )
        pins = generatevectors.compatibility_pins()
        changed = {
            name
            for name in original["files"]
            if pins["files"][name] != original["files"][name]
        }
        self.assertEqual(changed, {"openai-request.json", "openai-result.json"})
        self.assertEqual(pins["cases"], original["cases"])
        self.assertEqual(
            pins["files"]["openai-request.json"],
            "76a289b52c005b0debe10c171423df364be4cd0b5b70af312407dd59b944f617",
        )
        self.assertEqual(
            pins["files"]["openai-result.json"],
            "6766ed903c0a90d231d64d82600b539b22c0726dc91ba8b8e45f8a849a6075fe",
        )

    def test_surviving_portable_pins_are_not_regenerated(self) -> None:
        pins = generatevectors.compatibility_pins()
        retired = {
            row["path"]
            for row in json.loads(
                (ROOT / "spec/conformance/legacy-removal-v2.json").read_text()
            )["retired_cases"]
        }
        for name, sha256 in pins["files"].items():
            if name in retired:
                self.assertFalse((ROOT / "spec/conformance" / name).exists())
                continue
            with self.subTest(name=name):
                self.assertEqual(
                    hashlib.sha256(
                        (ROOT / "spec/conformance" / name).read_bytes()
                    ).hexdigest(),
                    sha256,
                )

    def isolated_bundle(self) -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        shutil.copytree(ROOT / "spec/conformance", root / "spec/conformance")
        return root, root / "spec/conformance"

    def test_provider_and_other_fixture_drift_is_rejected(self) -> None:
        root, bundle = self.isolated_bundle()
        baseline = json.loads((bundle / "baseline-v1-pins.json").read_text())
        # X4 wave 2 deletes the retired legacy fixtures; drift is checked on a surviving portable one.
        other = next(
            name
            for name in baseline["files"]
            if not name.startswith("openai-") and (bundle / name).exists()
        )
        for name in ("openai-request.json", "openai-result.json", other):
            path = bundle / name
            before = path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(before + b"\n")
                with self.assertRaisesRegex(
                    AssertionError, "Compatibility fixture changed: " + name
                ):
                    generatevectors.check_parity(root)
                path.write_bytes(before)

    def test_manifest_pin_and_nonhash_fields_and_duplicates_are_checked(self) -> None:
        root, bundle = self.isolated_bundle()
        path = bundle / "manifest.json"
        original = json.loads(path.read_text())
        # A retained baseline case (wave 2 keeps 74); current v2 cases are not pinned by the baseline.
        retained = json.loads((bundle / "legacy-removal-v2.json").read_text())[
            "retained_baseline_cases"
        ][0]["path"]
        for kind, name in (
            ("artifacts", "openai-request.json"),
            ("artifacts", "openai-result.json"),
            ("cases", retained),
        ):
            for mutation in ("sha256", "requirements", "duplicate"):
                manifest = json.loads(json.dumps(original))
                entry = next(item for item in manifest[kind] if item["path"] == name)
                with self.subTest(kind=kind, name=name, mutation=mutation):
                    if mutation == "duplicate":
                        manifest[kind].append(dict(entry))
                    elif mutation == "sha256":
                        entry["sha256"] = "0" * 64
                    else:
                        entry["requirements"] = []
                    path.write_text(json.dumps(manifest))
                    with self.assertRaisesRegex(
                        AssertionError, "Compatibility manifest entry changed: " + name
                    ):
                        generatevectors.check_parity(root)
        path.write_text(json.dumps(original))

    def test_supersession_cannot_rewrite_baseline_or_approval(self) -> None:
        root, bundle = self.isolated_bundle()
        for name in ("baseline-v1-pins.json", "baseline-v1-pin-supersessions.json"):
            path = bundle / name
            before = path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(before + b"\n")
                with self.assertRaisesRegex(AssertionError, "digest changed"):
                    generatevectors.check_parity(root)
                path.write_bytes(before)

    def test_missing_supersession_fails_closed(self) -> None:
        root, bundle = self.isolated_bundle()
        (bundle / "baseline-v1-pin-supersessions.json").unlink()
        with self.assertRaises(FileNotFoundError):
            generatevectors.check_parity(root)


if __name__ == "__main__":
    unittest.main()
