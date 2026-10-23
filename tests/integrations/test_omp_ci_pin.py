"""CI's OMP install pin matches the ACP backend it runs."""

from __future__ import annotations

from band.integrations.omp import OMP_PINNED_PACKAGE
from tests.paths import CI_SCRIPTS


class TestOmpCiPin:
    def test_setup_omp_script_pins_the_acp_package(self) -> None:
        script = (CI_SCRIPTS / "setup-omp.sh").read_text()
        assert OMP_PINNED_PACKAGE in script
