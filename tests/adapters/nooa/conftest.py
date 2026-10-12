"""Shared fixtures for the NOOA adapter tests."""

from __future__ import annotations

import pytest

from band.testing import FakeAgentTools

# NOOA supports Python 3.12 and 3.13 only; skip the package elsewhere.
pytest.importorskip("nooa")


@pytest.fixture
def tools() -> FakeAgentTools:
    """A fresh, call-tracking Band tool surface for one test."""
    return FakeAgentTools()
