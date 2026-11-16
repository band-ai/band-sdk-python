"""Guards for the opt-in suite gates (``tests/conftest.py``'s ``GATED_MARKERS``).

Two failure modes are pinned here because both would bite *inside a pytest
collection hook*, killing entire runs instead of failing one test:

* a set-but-empty gate var (``E2E_TESTS_ENABLED=``, as some CI wrappers export) must read as
  disabled, not raise a ValidationError — the ``env_ignore_empty`` behavior;
* the gate table must stay consistent with what it references: every field it
  names must exist on ``CollectionGateSettings``, and every marker it names
  must be registered in pyproject (a drift means a gated suite either warns as
  unknown or never skips).
"""

from __future__ import annotations

import tomllib

import pytest

from tests.conftest import GATED_MARKERS, CollectionGateSettings
from tests.e2e.baseline.settings import BaselineSettings
from tests.paths import REPO_ROOT


def registered_pytest_markers() -> set[str]:
    """The marker names registered in pyproject's ``[tool.pytest.ini_options]``."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    entries: list[str] = pyproject["tool"]["pytest"]["ini_options"]["markers"]
    return {entry.split(":", 1)[0].strip() for entry in entries}


GATE_FIELDS = list(GATED_MARKERS.values())


class TestOptInGates:
    @pytest.mark.parametrize("field", GATE_FIELDS)
    def test_empty_gate_var_reads_as_disabled(
        self, monkeypatch: pytest.MonkeyPatch, field: str
    ) -> None:
        monkeypatch.setenv(field.upper(), "")
        assert getattr(CollectionGateSettings(), field) is False

    @pytest.mark.parametrize("field", GATE_FIELDS)
    def test_gated_marker_opens_with_a_real_settings_field(self, field: str) -> None:
        assert field in CollectionGateSettings.model_fields

    def test_every_gated_marker_is_registered_in_pyproject(self) -> None:
        assert set(GATED_MARKERS) <= registered_pytest_markers()

    def test_empty_baseline_vars_fall_back_to_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("E2E_TESTS_ENABLED", "")
        monkeypatch.setenv("E2E_TIMEOUT", "")
        settings = BaselineSettings()
        assert settings.e2e_tests_enabled is False
        assert settings.e2e_timeout == 120
