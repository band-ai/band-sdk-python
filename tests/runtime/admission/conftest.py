"""Fixtures: one shared timeline, the platform peer and the model over it."""

from __future__ import annotations

import random

import pytest

from tests.runtime.admission.barriers import Model, Platform


@pytest.fixture(autouse=True)
def periodic_wake_after_the_full_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the jittered first periodic wake to the interval's end.

    A jitter that is not a whole number of microseconds leaves looptime's integer
    clock just short of that timer, so a virtual jump past it never fires.
    """
    monkeypatch.setattr(random, "uniform", lambda lower, upper: upper)


@pytest.fixture
def journal() -> list[str]:
    return []


@pytest.fixture
def platform(journal: list[str]) -> Platform:
    return Platform(journal=journal)


@pytest.fixture
def model(journal: list[str]) -> Model:
    return Model(journal=journal)
