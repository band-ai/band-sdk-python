"""GeneratedArtifact specification regression checks."""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import shutil
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

from contracts.spec.tools import checkspec
from tests.contracts.spec.workspace import link_or_copy


class TestGeneratedArtifact(unittest.TestCase):
    def test_importing_generators_does_not_write_files(self) -> None:
        with patch.object(
            Path, "write_text", side_effect=AssertionError("import wrote a file")
        ):
            for name in ("buildschemas", "writerequirements", "buildserviceschema"):
                importlib.reload(
                    importlib.import_module("contracts.spec.tools." + name)
                )

    def check_mutation(
        self, relative: str, mutate: Callable[[dict[str, Any]], None], diagnostic: str
    ) -> None:
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
            path = root / relative
            value = json.loads(path.read_text())
            mutate(value)
            changed = json.dumps(value, indent=2) + "\n"
            path.write_text(changed)
            with (
                patch.object(checkspec, "ROOT", root),
                patch("sys.argv", ["checkspec", "--schema-only"]),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(AssertionError, diagnostic),
            ):
                checkspec.main()
            self.assertEqual(
                path.read_text(),
                changed,
                "validation must not regenerate a stale artifact",
            )

    def test_protocol_schema_drift_is_rejected_without_rewriting(self) -> None:
        self.check_mutation(
            "spec/schemas/protocol.schema.json",
            lambda schema: schema["$defs"]["id"].update(maxLength=255),
            "Regenerate the protocol schema",
        )

    def test_requirements_drift_is_rejected_without_rewriting(self) -> None:
        self.check_mutation(
            "spec/requirements.json",
            lambda catalog: catalog["requirements"][0].update(
                statement="The loader MUST support only native discovery."
            ),
            "Regenerate the requirements catalog",
        )

    def test_manifest_cannot_mislabel_a_case_requirement(self) -> None:
        self.check_mutation(
            "spec/conformance/manifest.json",
            lambda manifest: manifest["cases"][0].update(requirements=[]),
            "Case requirements differ from manifest",
        )

    def test_artifact_cannot_claim_an_unknown_requirement(self) -> None:
        self.check_mutation(
            "spec/conformance/manifest.json",
            lambda manifest: manifest["artifacts"][0].update(
                requirements=["UNKNOWN-001"]
            ),
            "Unknown artifact requirement",
        )

    def test_coverage_cannot_drop_examples(self) -> None:
        self.check_mutation(
            "spec/conformance/coverage.json",
            lambda index: index["requirements"][3]["examples"].update(cases=[]),
            "Regenerate the coverage index",
        )

    def test_manifest_cannot_omit_a_retained_case(self) -> None:
        self.check_mutation(
            "spec/conformance/manifest.json",
            lambda manifest: manifest["cases"].pop(),
            "Case inventory differs from manifest",
        )
