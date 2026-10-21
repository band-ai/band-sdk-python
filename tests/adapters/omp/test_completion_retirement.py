"""Unproven retirement keeps the room owned across the adapter seam."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.integrations import omprpc
from band.integrations.omprpc import OmpCompletionTimeout, OmpProcessError
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.omp.kit import run_turn
from tests.integrations.omprpc.kit import OMP_PEER_SCRIPT

pytest.importorskip("psutil", reason="psutil not installed (band-sdk[omp])")


class HoldInput(BaseModel):
    """Zero-field model for the cancellation-resistant hold tool."""


def _hold(gate: asyncio.Event):  # CustomToolDef handler
    async def hold(_inp: HoldInput) -> str:
        while True:
            try:
                await gate.wait()
            except asyncio.CancelledError:  # cancellation-resistant callback
                continue
            return "released"

    return hold


@pytest.fixture(autouse=True)
def native_startup_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", AsyncMock())


class TestUnprovenRetirement:
    async def test_survivor_blocks_successor_until_callback_finishes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(omprpc, "COMPLETION_TIMEOUT_S", 0.25)
        gate = asyncio.Event()
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=str(tmp_path),
                command=(
                    sys.executable,
                    "-u",
                    OMP_PEER_SCRIPT,
                    "--scenario",
                    "drain-stubborn-adapter",
                ),
                turn_timeout_s=30.0,
            ),
            emit=(),
            additional_tools=[(HoldInput, _hold(gate))],
        )

        try:
            tools1 = FakeAgentTools()
            async with asyncio.timeout(20):
                with pytest.raises(OmpCompletionTimeout, match="uncertain"):
                    await run_turn(adapter, tools1)
            assert reported_failures(tools1)
            room = adapter._rooms.get("room-123")
            assert room is not None  # unproven retirement: the room stays owned

            # A successor must not launch a replacement transport while the
            # survivor keeps the retirement unproven.
            tools2 = FakeAgentTools()
            async with asyncio.timeout(20):
                with pytest.raises(
                    (OmpProcessError, BaseExceptionGroup),
                    match="retirement is unproven",
                ):
                    await run_turn(adapter, tools2, bootstrap=False)
            assert adapter._rooms.get("room-123") is room  # not replaced
            assert not tools2.turn.replied  # no synthesized success anywhere

            gate.set()  # the callback completes; retirement becomes provable
            with contextlib.suppress(TurnResultAlreadyReported):
                # The stub peer never replies; an unsettled-but-run turn is
                # the honest disposition here. What matters is the seam:
                async with asyncio.timeout(30):
                    await run_turn(adapter, FakeAgentTools(), bootstrap=False)
            replacement = adapter._rooms["room-123"]
            assert replacement is not room  # replaced only after proof
            proc = room.runtime._proc
            assert proc is None or proc.returncode is not None  # survivor reaped
            assert await replacement.runtime.request("checkpoint")  # live
        finally:
            gate.set()  # never leak a cancellation-resistant callback
