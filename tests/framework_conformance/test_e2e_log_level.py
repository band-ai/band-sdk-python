"""The diagnostic log level reaches the Band and Parlant loggers."""

from __future__ import annotations

import logging

import pytest

from tests.e2e.baseline.toolkit.logs import apply_run_log_level


class TestRunLogLevel:
    def test_empty_level_leaves_loggers_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BAND_E2E_LOG_LEVEL", raising=False)
        self._assert_levels_after(logging.WARNING, logging.WARNING)

    def test_debug_sets_band_and_parlant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BAND_E2E_LOG_LEVEL", "DEBUG")
        self._assert_levels_after(logging.DEBUG, logging.DEBUG)

    def _assert_levels_after(self, band_level: int, parlant_level: int) -> None:
        band = logging.getLogger("band")
        parlant = logging.getLogger("parlant")
        previous = (band.level, parlant.level)
        band.setLevel(logging.WARNING)
        parlant.setLevel(logging.WARNING)
        try:
            apply_run_log_level()
            assert (band.level, parlant.level) == (band_level, parlant_level)
        finally:
            band.setLevel(previous[0])
            parlant.setLevel(previous[1])
