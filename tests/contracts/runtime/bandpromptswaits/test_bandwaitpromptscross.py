from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

from remote_agent.band_format import missing_prompts
from tests.contracts.runtime.bandpromptswaits.kit import LUCAS_PROMPT_CASES
from tests.contracts.runtime.formatter import PROMPTS_SCRIPT
from tests.contracts.runtime.settings import runtime_settings


@pytest.mark.contract_engine
class TestBandWaitPromptsCross(unittest.TestCase):
    def test_lucas_five_cases_match_mix_and_expected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = []
            for i, vector in enumerate(LUCAS_PROMPT_CASES):
                path = root / f"wait{i}.band"
                path.write_text(vector["input"])
                files.append(path)
            batch, script = root / "files.json", root / "prompts.exs"
            batch.write_text(json.dumps([str(p) for p in files]))
            script.write_text(PROMPTS_SCRIPT)
            result = subprocess.run(
                ["mix", "run", "--no-start", str(script), str(batch)],
                capture_output=True,
                text=True,
                timeout=900,
                cwd=runtime_settings().band_contract_lab,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        outputs = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(len(outputs), len(LUCAS_PROMPT_CASES))
        for vector, actual in zip(LUCAS_PROMPT_CASES, outputs):
            with self.subTest(vector=vector["id"]):
                self.assertEqual(actual, {"missing": vector["missing"]})
                self.assertEqual(
                    missing_prompts(vector["input"].encode()), actual["missing"]
                )
