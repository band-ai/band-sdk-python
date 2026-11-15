"""Run a native OMP adapter against a scripted conformance peer.

The peer is a Python module launched as the adapter's OMP command, so each
probe drives the adapter's real RPC client. The omp extra is imported inside
the helper: not every lane's venv installs it.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Unpack

import pytest

from band.core.types import FeatureKwargs
from tests.paths import REPO_ROOT

if TYPE_CHECKING:
    from band.adapters.omp import OmpAdapter


@asynccontextmanager
async def started_omp_adapter(
    peer_module: str, *peer_args: str, **features: Unpack[FeatureKwargs]
) -> AsyncIterator[OmpAdapter]:
    """Start an ``OmpAdapter`` running ``peer_module``; clean it up on exit."""
    pytest.importorskip("psutil")
    from band.adapters.omp import (  # noqa: PLC0415 -- requires the optional omp extra
        OmpAdapter,
        OmpAdapterConfig,
    )

    with tempfile.TemporaryDirectory(prefix="band-conformance-omp-") as workspace:
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=workspace,
                command=(sys.executable, "-u", "-m", peer_module, *peer_args),
                env={"PYTHONPATH": str(REPO_ROOT)},
            ),
            **features,
        )
        try:
            await adapter.on_started("Agent", "A coding agent")
            yield adapter
        finally:
            await adapter.cleanup_all()
