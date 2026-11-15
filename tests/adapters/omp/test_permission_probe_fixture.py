"""The live OMP permission probe stays aligned with Python constants."""

from __future__ import annotations

from band.core.types import PERMISSION_CANCELLED
from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from tests.e2e.baseline.smoke.samples.omppermission import FORM_PROBE_TITLE
from tests.paths import REPO_ROOT

_PROBE = (
    REPO_ROOT / "tests" / "e2e" / "baseline" / "fixtures" / "omp-permission-probe.js"
)


class TestPermissionProbeFixture:
    def test_js_probe_uses_the_python_form_constants(self) -> None:
        text = _PROBE.read_text(encoding="utf-8")
        assert FORM_PROBE_TITLE in text
        assert OMP_FORM_APPROVE in text
        assert OMP_FORM_DENY in text
        assert PERMISSION_CANCELLED in text
