"""Shared collection and missing-extra gates work without ACP installed."""

from __future__ import annotations

import subprocess
import sys

from tests.paths import REPO_ROOT


class TestAntigravityToolkitImports:
    def test_shared_toolkit_imports_and_gate_work_without_acp(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
# Python's absent-module sentinel rejects an accidental optional-extra import.
sys.modules["acp"] = None
from tests.e2e.baseline.settings import Backends, BaselineSettings, LLMCredentials
from tests.e2e.baseline.toolkit.adapters import specs
from tests.e2e.baseline.toolkit.deps import Dep, requirement_reason
settings = BaselineSettings(
    backends=Backends(antigravity_command=(sys.executable,)),
    llm_credentials=LLMCredentials(gemini_api_key="key", google_api_key=""),
)
assert specs()
assert requirement_reason(Dep.ANTIGRAVITY, settings) is not None
""",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        assert result.returncode == 0, result.stderr
