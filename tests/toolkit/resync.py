"""Deterministic periodic resync for tests that run a real room scheduler."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def periodic_wake_after_the_full_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a room's jittered first periodic resync to the interval's end.

    A wake at a random point lands inside a test's quiet window, and a jitter that
    is not a whole number of microseconds leaves looptime's integer clock just
    short of that timer, so a virtual jump past it never fires.
    """
    monkeypatch.setattr(
        "band.runtime.resync.first_wake_offset", lambda interval: interval
    )
