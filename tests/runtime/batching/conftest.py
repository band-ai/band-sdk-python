"""Fixtures shared by the batching suites."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.runtime.batching.kit import FakeRoom, room_context


@pytest.fixture
def ctx() -> MagicMock:
    """An uninitialized room context with known participants."""
    return room_context()


@pytest.fixture
def platform(mock_rest_client: MagicMock) -> FakeRoom:
    return FakeRoom(rest=mock_rest_client)
