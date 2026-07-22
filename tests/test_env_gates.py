"""Set-but-empty env vars must not kill settings construction.

Some CI wrappers export gates as empty (``CI=``, ``E2E_TESTS_ENABLED=``).
Without ``env_ignore_empty=True`` pydantic-settings tries to parse ``""`` as
the field's bool/int type and raises a ValidationError — for the collection
gates that happens *inside a pytest collection hook*, killing the entire run.
These guards pin the empty-means-unset behavior for both settings surfaces.
"""

from __future__ import annotations

import pytest

from tests.conftest import CollectionGateSettings
from tests.e2e.baseline.settings import BaselineSettings


def test_empty_collection_gate_vars_read_as_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in (
        "CI",
        "E2E_TESTS_ENABLED",
        "DOCKER_TESTS_ENABLED",
        "SANDBOX_TESTS_ENABLED",
    ):
        monkeypatch.setenv(var, "")
    gates = CollectionGateSettings()
    assert gates.ci is False
    assert gates.e2e_tests_enabled is False
    assert gates.docker_tests_enabled is False
    assert gates.sandbox_tests_enabled is False


def test_empty_baseline_vars_fall_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2E_TESTS_ENABLED", "")
    monkeypatch.setenv("E2E_TIMEOUT", "")
    settings = BaselineSettings()
    assert settings.e2e_tests_enabled is False
    assert settings.e2e_timeout == 120
