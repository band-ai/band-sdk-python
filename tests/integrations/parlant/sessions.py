"""Isolation for the process-wide Parlant session registry."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from band.integrations.parlant.sessiontools import _session_tools


@pytest.fixture(autouse=True)
def clear_session_tools() -> Iterator[None]:
    """Each test starts and ends with no Parlant session bound to a room."""
    _session_tools.clear()
    yield
    _session_tools.clear()
