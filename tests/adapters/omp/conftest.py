"""Native OMP startup fixtures."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from tests.adapters.omp.selectionkit import (
    MODEL_PROVIDER,
    ORIGINAL_MODEL,
    SELECTED_EFFORT,
    OverlappingSelections,
    RollbackRuntime,
)


@pytest.fixture(autouse=True)
def native_startup_ready(monkeypatch):
    ready = AsyncMock()
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", ready)
    return ready


@pytest.fixture
async def overlapping_selections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[tuple[OmpAdapter, RollbackRuntime, OverlappingSelections]]:
    runtime = RollbackRuntime()
    resolver = OverlappingSelections(runtime)
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
    adapter = OmpAdapter(
        OmpAdapterConfig(
            cwd=str(tmp_path),
            model=f"{MODEL_PROVIDER}/{ORIGINAL_MODEL}",
            reasoning_effort=SELECTED_EFFORT,
        ),
        resolve_permission=resolver,
        emit=(),
    )
    resolver.adapter = adapter
    try:
        yield adapter, runtime, resolver
    finally:
        resolver.release.set()
        runtime.release_rollback.set()
        await adapter.cleanup_all()


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
