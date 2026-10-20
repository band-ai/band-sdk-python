from __future__ import annotations

from pathlib import Path

from tests.framework_conformance import venv_job_coverage as vjc
from tests.framework_conformance.crewaicoverage.kit import (
    _CREWAI_ONLY_MODULES,
)


class TestDetectionFindsTheTestsThatOnlyTheCrewaiVenvCanRun:
    def test_detection_finds_the_tests_that_only_the_crewai_venv_can_run(self) -> None:
        """Guard the guard: if the pattern drifts, the tests above pass empty.

        These are the whole set today — `phase3` for its nest_asyncio cases,
        `test_files_image_passthrough_matrix.py` for its guarded `import crewai`
        (real-package probe, `_CREWAI_AVAILABLE` skip elsewhere), the rest for
        importing crewai itself.
        """
        pattern = vjc.needs_venv_pattern(frozenset(_CREWAI_ONLY_MODULES.values()))
        needed = vjc.tests_needing_venv(pattern)
        assert needed == {
            Path("tests/integrations/crewai/activity/test_execution.py"),
            Path("tests/adapters/test_crewai_flow_phase3.py"),
            Path("tests/adapters/crewaiworker/test_native_settlement.py"),
            Path("tests/adapters/crewaiworker/test_flow_admission.py"),
            Path("tests/adapters/crewaiworker/test_parallel_settlement.py"),
            Path("tests/integrations/test_crewai_flow_real_sdk.py"),
            Path("tests/integrations/test_crewai_real_tools.py"),
            Path("tests/integrations/test_crewai_completion.py"),
            Path("tests/test_capability_gating_e2e.py"),
            Path("tests/framework_conformance/test_files_image_passthrough_matrix.py"),
        }
