"""Fixtures for the real-wire link close suites."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest

from band.client.streaming.client import RECONNECT_POLICY


@pytest.fixture(scope="class")
def millisecond_reconnects() -> Iterator[None]:
    """The transport's reconnect backoff shrunk to milliseconds, so a generated
    run of closes spends its time on the scenario, not on the transport's
    timers. Class-scoped because Hypothesis rejects function-scoped fixtures."""
    fast = replace(RECONNECT_POLICY, base_delay_s=0.001, rapid_disconnect_uptime_s=0.0)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("band.client.streaming.client.RECONNECT_POLICY", fast)
        yield
