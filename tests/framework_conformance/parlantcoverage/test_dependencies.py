from __future__ import annotations

from tests.framework_conformance import venv_job_coverage as vjc
from tests.framework_conformance.parlantcoverage.kit import (
    _PARLANT_ONLY_MODULES,
)


class TestParlantOnlyDependencySetMatchesPyproject:
    def test_parlant_only_dependency_set_matches_pyproject(self) -> None:
        """The detected module set is derived from a real dep list, not a guess."""
        assert vjc.only_distributions("dev-parlant") == set(_PARLANT_ONLY_MODULES), (
            "the dev-parlant-only dependencies changed; update _PARLANT_ONLY_MODULES so "
            "this guard still detects every test that needs that venv"
        )
