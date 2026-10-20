from __future__ import annotations

from pathlib import Path

from tests.framework_conformance import venv_job_coverage as vjc
from tests.framework_conformance.parlantcoverage.kit import (
    _PARLANT_ONLY_MODULES,
)


class TestDetectionFindsTheTestsThatOnlyTheParlantVenvCanRun:
    def test_detection_finds_the_tests_that_only_the_parlant_venv_can_run(self) -> None:
        """Guard the guard: if the pattern drifts, the tests above pass empty."""
        pattern = vjc.needs_venv_pattern(frozenset(_PARLANT_ONLY_MODULES.values()))
        needed = vjc.tests_needing_venv(pattern)
        assert needed == {
            Path("tests/integrations/parlant/activity/test_execution.py"),
            Path("tests/integrations/parlant/test_catalog.py"),
            Path("tests/integrations/parlant/test_chattools.py"),
            Path("tests/integrations/parlant/test_contacttools.py"),
            Path("tests/integrations/parlant/test_customtools.py"),
            Path("tests/integrations/parlant/test_filetools.py"),
            Path("tests/integrations/parlant/test_guard.py"),
            Path("tests/integrations/parlant/test_preambletask.py"),
            Path("tests/integrations/parlant/test_selection.py"),
            Path("tests/integrations/parlant/test_server.py"),
            Path("tests/integrations/parlant/test_tasktools.py"),
            Path("tests/integrations/parlant/test_vocabulary.py"),
        }
