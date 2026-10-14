"""The owning legacy job must collect every real legacy package consumer."""

from __future__ import annotations

import importlib.util
import sys

import pytest

from tests.e2e.baseline.toolkit.deps import Extra
from tests.framework_configs.adapters import ADAPTER_CONFIGS
from tests.framework_configs.converters import CONVERTER_CONFIGS
from tests.framework_conformance import venv_job_coverage as coverage
from tests.paths import REPO_ROOT


class TestLegacyJobCoverage:
    @pytest.fixture
    def legacy_environment(self) -> None:
        if (
            coverage.CoverageSettings().BAND_TEST_EXTRA != Extra.DEV_LEGACY
            and importlib.util.find_spec("strands") is None
        ):
            pytest.skip("covered by dev-legacy")

    def test_expected_configs_are_present(self, legacy_environment: None) -> None:
        assert importlib.util.find_spec("strands") is not None
        expected = {"strands"}
        if sys.version_info[:2] in {(3, 12), (3, 13)} and sys.platform != "win32":
            assert importlib.util.find_spec("nooa") is not None
            expected.add("nooa")
        assert expected <= {config.framework_id for config in ADAPTER_CONFIGS}
        assert expected <= {config.framework_id for config in CONVERTER_CONFIGS}

    def test_job_selects_every_required_node(self, legacy_environment: None) -> None:
        needed = coverage.tests_needing_venv(
            coverage.needs_venv_pattern(frozenset({"strands", "nooa"}))
        )
        assert needed, "legacy dependency detection found no consumers"
        assert all((REPO_ROOT / path).exists() for path in needed)
        arguments = coverage.pytest_arguments(
            coverage.job_command("test-legacy", "Run legacy tests")
        )
        selected = coverage.selected_nodes(arguments)
        # Directory collection honors NOOA's supported-platform conftest gate.
        nooa_tests = REPO_ROOT / "tests/adapters/nooa"
        targets = {
            str(nooa_tests) if (REPO_ROOT / path).parent == nooa_tests else str(path)
            for path in needed
        }
        baseline = coverage.collected_nodes(
            [*sorted(targets), "tests/framework_conformance/"]
        )
        needed_files = {path.as_posix() for path in needed}
        required = {
            node.nodeid
            for node in baseline
            if node.frameworks & {"strands", "nooa"}
            or (
                not node.frameworks
                and not node.skipped
                and node.nodeid.split("::", 1)[0] in needed_files
            )
        }
        assert required, "no legacy nodes collected"
        assert not required - selected, sorted(required - selected)
        assert any("test_strands_injection_spike.py" in node for node in selected)

    def test_job_paths_exist(self) -> None:
        command = coverage.job_command("test-legacy", "Run legacy tests")
        assert all((REPO_ROOT / path).exists() for path in coverage.job_paths(command))
