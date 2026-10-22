from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

from remote_agent.band_format import missing_prompts
from tests.contracts.runtime.formatter import (
    PROMPT_CONTRACTS,
    PROMPTS_COMPLETE,
    PROMPTS_PARTIAL,
    PROMPTS_SCRIPT,
    band_files,
)
from tests.contracts.runtime.settings import runtime_settings


@pytest.mark.contract_engine
class TestRequirePromptsCross(unittest.TestCase):
    def test_missing_prompts_match_mix_contract_check_on_every_repo_contract(
        self,
    ) -> None:
        files = [p for p in band_files() if p.read_bytes().startswith(b"---\n")]
        self.assertGreaterEqual(len(files), 40)
        extra = Path(tempfile.mkdtemp(prefix="prompts-cross-"))
        self.addCleanup(lambda: shutil.rmtree(extra))
        for i, text in enumerate(
            [PROMPTS_COMPLETE, PROMPTS_PARTIAL, *PROMPT_CONTRACTS]
        ):
            (extra / f"case{i}.band").write_text(text)
            files.append(extra / f"case{i}.band")
        with tempfile.TemporaryDirectory(prefix="prompts-cross-") as tmp:
            batch, script = Path(tmp) / "files.json", Path(tmp) / "prompts.exs"
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
        expected = json.loads(result.stdout.strip().splitlines()[-1])
        errors = [(str(f), e["error"]) for f, e in zip(files, expected) if "error" in e]
        self.assertEqual(errors, [])
        mismatches = [
            str(f)
            for f, e in zip(files, expected)
            if missing_prompts(f.read_bytes()) != e["missing"]
        ]
        self.assertEqual(mismatches, [])
        self.assertTrue(any(e["missing"] for e in expected))
