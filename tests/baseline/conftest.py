"""Shared fixtures for deterministic baseline conformance tests."""

from __future__ import annotations

import pytest

from tests.baseline.platform import BaselineTools


@pytest.fixture
def baseline_tools() -> BaselineTools:
    """Fresh in-memory platform surface for one scenario."""
    return BaselineTools()
