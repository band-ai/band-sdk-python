"""Native OMP startup fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def native_startup_ready(monkeypatch):
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", AsyncMock())
