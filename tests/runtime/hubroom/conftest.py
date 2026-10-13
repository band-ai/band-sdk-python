"""Fixtures for hub-room contact tool tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_rest():
    return MagicMock()
