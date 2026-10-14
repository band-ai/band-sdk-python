"""Fixtures shared by the delivery suites."""

from __future__ import annotations

import random

import pytest

from tests.runtime.delivery.kit import RestPeer


@pytest.fixture(autouse=True)
def periodic_wake_after_the_full_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first periodic wake is spread over the interval; pin it to the end."""
    monkeypatch.setattr(random, "uniform", lambda lower, upper: upper)


@pytest.fixture
def rest_peer() -> RestPeer:
    return RestPeer()
