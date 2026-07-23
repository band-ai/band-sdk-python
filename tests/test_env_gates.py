"""Guards for the opt-in suite gates (``tests/conftest.py``'s ``GATED_MARKERS``).

Two failure modes are pinned here because both would bite *inside a pytest
collection hook*, killing entire runs instead of failing one test:

* a set-but-empty gate var (``CI=``, as some CI wrappers export) must read as
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


def test_empty_collection_gate_vars_read_as_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate_fields = ["ci", *GATED_MARKERS.values()]
    for field in gate_fields:
        monkeypatch.setenv(field.upper(), "")
    gates = CollectionGateSettings()
    for field in gate_fields:
        assert getattr(gates, field) is False, field


def test_every_gated_marker_opens_with_a_real_settings_field() -> None:
    gates = CollectionGateSettings()
    for field in GATED_MARKERS.values():
        assert hasattr(gates, field), field


def test_every_gated_marker_is_registered_in_pyproject() -> None:
    assert set(GATED_MARKERS) <= registered_pytest_markers()


def test_empty_baseline_vars_fall_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2E_TESTS_ENABLED", "")
    monkeypatch.setenv("E2E_TIMEOUT", "")
    settings = BaselineSettings()
    assert settings.e2e_tests_enabled is False
    assert settings.e2e_timeout == 120
