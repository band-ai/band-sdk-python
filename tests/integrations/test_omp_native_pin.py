"""CI's OMP install pin matches the native adapter pin."""

from __future__ import annotations

from band.integrations.ompprocess import OMP_NATIVE_PINNED_PACKAGE
from tests.paths import CI_SCRIPTS


class TestOmpNativePin:
    def test_setup_omp_script_pins_the_native_package(self) -> None:
        script = (CI_SCRIPTS / "setup-omp.sh").read_text()
        assert OMP_NATIVE_PINNED_PACKAGE in script
