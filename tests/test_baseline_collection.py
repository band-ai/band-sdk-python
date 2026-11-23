"""Baseline fixture rules apply only inside the suite that provides the fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.e2e import baseline
from tests.paths import REPO_ROOT


class TestBaselineCollection:
    def test_agent_parameter_is_checked_only_inside_the_baseline_suite(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
        monkeypatch.setenv("E2E_TESTS_ENABLED", "false")
        suite = pytester.mkdir("live")
        shutil.copyfile(
            Path(baseline.__file__).with_name("conftest.py"), suite / "conftest.py"
        )
        (suite / "test_live.py").write_text("def test_live(): pass\n")
        ordinary = pytester.makepyfile(
            test_ordinary="""
            import pytest

            @pytest.mark.parametrize("agent", ["local"])
            def test_ordinary(agent):
                assert agent == "local"
            """
        )

        mixed = pytester.runpytest_subprocess("-q")
        mixed.assert_outcomes(passed=1, skipped=1)

        ordinary.rename(suite / ordinary.name)
        inside = pytester.runpytest_subprocess("--collect-only", "-q")
        assert inside.ret == pytest.ExitCode.USAGE_ERROR
        inside.stderr.fnmatch_lines(["*test_ordinary*no @per_adapter*"])
