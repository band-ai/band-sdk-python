"""Native OMP startup fixtures."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig


@pytest.fixture(autouse=True)
def native_startup_ready(monkeypatch):
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", AsyncMock())


@pytest.fixture
def ask_adapter(tmp_path: Path):
    """An adapter whose native peer raises a structured ask dialog."""
    adapter = OmpAdapter(
        OmpAdapterConfig(
            cwd=str(tmp_path),
            command=(
                sys.executable,
                "-u",
                "-m",
                "tests.integrations.ompaskpeer",
                "ask",
            ),
        ),
        emit=(),
    )
    return adapter
