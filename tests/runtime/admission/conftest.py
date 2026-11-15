"""Fixtures: one shared timeline, the platform peer and the model over it."""

from __future__ import annotations

import pytest

from tests.runtime.admission.barriers import Model, Platform


@pytest.fixture
def journal() -> list[str]:
    return []


@pytest.fixture
def platform(journal: list[str]) -> Platform:
    return Platform(journal=journal)


@pytest.fixture
def model(journal: list[str]) -> Model:
    return Model(journal=journal)
