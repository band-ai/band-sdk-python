"""Shared fixtures for the Agno adapter tests.

(``sample_platform_message`` comes from the root ``tests/conftest.py``.)
"""

from __future__ import annotations

import pytest

from band.testing import FakeAgentTools


@pytest.fixture
def tools() -> FakeAgentTools:
    """A fresh, call-tracking Band tool surface for one test."""
    return FakeAgentTools()
