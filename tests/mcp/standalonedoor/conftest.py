"""Fixtures for the standalone door: the platform peer behind the HTTP boundary."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from tests.mcp.standalonedoor.peer import Platform


@pytest.fixture
def platform(httpx_mock: HTTPXMock) -> Platform:
    platform = Platform()
    httpx_mock.add_callback(platform.answer, is_reusable=True)
    return platform
