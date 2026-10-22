"""Reply evidence must be portable without changing historical observations."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import contracts.spec.tools.repliesadversarial as runner
from tests.contracts.spec.workspace import isolated_root, run_tool
from tests.paths import CONTRACTS_ROOT as ROOT

RUNNER = ROOT / "spec/tools/repliesadversarial.py"


class TestRepliesEvidencePaths(unittest.TestCase):
    def test_nested_evidence_paths_are_portable_without_changing_observations(
        self,
    ) -> None:
        observed = {
            "commands": [
                {
                    "argv": [
                        str(ROOT / "spec/tool.py"),
                        str(Path.home() / "external/old-repo"),
                    ],
                    "exit_code": 1,
                }
            ],
            "paths": {str(ROOT / "spec/input.json"): str(Path.home())},
            "passed": 4,
            "failed": 49,
            "elapsed_ms": 911,
            "source_hash": "abc123",
            "created_at": "2026-10-15T07:36:02Z",
            "boundary": str(Path.home()) + "-other/file",
            "error": "Build " + str(Path.home() / "external/contract-lab") + " first",
            "checkout": str(ROOT),
        }
        expected = dict(
            observed,
            commands=[
                {"argv": ["spec/tool.py", "~/external/old-repo"], "exit_code": 1}
            ],
            paths={"spec/input.json": "~"},
            error="Build ~/external/contract-lab first",
            checkout=".",
        )
        self.assertEqual(json.loads(runner.evidence_json(observed)), expected)
        self.assertEqual(observed["passed"], 4)
        self.assertTrue(observed["commands"][0]["argv"][0].startswith(str(ROOT)))

    def test_cli_normalizes_existing_evidence_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = isolated_root(temp)
            copied = root / "spec/conformance/replies-adversarial"
            evidence = copied / "evidence"
            evidence.mkdir(exist_ok=True)
            path = evidence / "validation.json"
            path.write_text(
                json.dumps(
                    {
                        "argv": [
                            str(Path.home() / "external/python3"),
                            str(root / "spec/tools" / RUNNER.name),
                        ],
                        "exit_code": 1,
                        "passed": 4,
                        "failed": 49,
                    }
                )
            )
            first = run_tool(root, RUNNER.name, "--normalize-evidence")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(
                json.loads(path.read_text()),
                {
                    "argv": ["~/external/python3", "spec/tools/" + RUNNER.name],
                    "exit_code": 1,
                    "passed": 4,
                    "failed": 49,
                },
            )
            before = path.read_bytes()
            second = run_tool(root, RUNNER.name, "--normalize-evidence")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(path.read_bytes(), before)

    def test_spec_data_has_no_absolute_home_paths(self) -> None:
        needle = b"/" + b"home/"
        hits = [
            str(path.relative_to(ROOT))
            for path in (ROOT / "spec").rglob("*")
            if path.is_file()
            and path.suffix in {".json", ".md", ".band", ".yaml", ".yml"}
            and needle in path.read_bytes()
        ]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
