"""Current-profile release drills must work without the legacy contract."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from contracts.deploy import smokerelease
from contracts.deploy.releasecontract import contract_path, file_source, smoke_contracts
from tests.paths import CONTRACTS_ROOT

ROOT = CONTRACTS_ROOT


class TestCurrentProfile(unittest.TestCase):
    def test_release_drills_use_current_contract_without_legacy_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory)
            (release / "contracts/v2").mkdir(parents=True)
            shutil.copy2(
                ROOT / "contract-lab/contracts/v2/hybrid-assistant.band",
                release / "contracts/v2/hybrid-assistant.band",
            )
            selected = contract_path(release)
            self.assertEqual(selected, release / "contracts/v2/hybrid-assistant.band")
            source = file_source(selected)
            self.assertEqual(source["format"], "frontmatter")
            self.assertEqual(source["profile"], "band-contract/file-source/1")
            self.assertEqual(
                source["sha256"], hashlib.sha256(selected.read_bytes()).hexdigest()
            )
            self.assertIn("engine: 0.2.0-lab.1", source["content"])
            paths = smoke_contracts(release)
            self.assertEqual(
                {format for _, format in paths}, {"frontmatter", "standard"}
            )
            for path, _ in paths:
                self.assertTrue(path.is_file())
            expiring = file_source(selected, clock="1s")
            self.assertIn("contract @1s => expired", expiring["content"])
            self.assertNotEqual(expiring["sha256"], source["sha256"])
            self.assertEqual(
                expiring["sha256"],
                hashlib.sha256(expiring["content"].encode()).hexdigest(),
            )
            self.assertEqual(selected.read_text(), source["content"])

    def test_deploy_service_examples_select_frontmatter_explicitly(self) -> None:
        for path in (ROOT / "deploy/examples").glob("*.json"):
            self.assertEqual(
                json.loads(path.read_text())["engine"]["format"],
                "frontmatter",
                str(path),
            )

    def test_smoke_engine_calls_require_explicit_format(self) -> None:
        for node in ast.walk(ast.parse(smokerelease.PROBE)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "EngineProcess"
            ):
                self.assertIn("format", {key.arg for key in node.keywords})
