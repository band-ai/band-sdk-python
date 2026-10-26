"""Run OMP turn-outcome conformance through the real RPC client."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.turnprobes import (
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
)
from tests.paths import REPO_ROOT


async def run_omp(script: TurnScript, tools: FakeAgentTools) -> None:
    pytest.importorskip("psutil")
    from band.adapters.omp import (  # noqa: PLC0415 -- requires the optional omp extra
        OmpAdapter,
        OmpAdapterConfig,
    )

    with tempfile.TemporaryDirectory(prefix="band-conformance-omp-") as workspace:
        script_path = Path(workspace) / "turn.json"
        script_path.write_bytes(TypeAdapter(TurnScript).dump_json(script))
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=workspace,
                command=(
                    sys.executable,
                    "-u",
                    "-m",
                    "tests.framework_conformance.turnprobes.omppeer",
                    str(script_path),
                ),
                env={"PYTHONPATH": str(REPO_ROOT)},
            )
        )
        try:
            await adapter.on_started("Agent", "A coding agent")
            await adapter.on_event(turn_input(tools))
        finally:
            await adapter.cleanup_all()


PROBES: dict[str, TurnOutcomeProbe] = {
    "omp": TurnOutcomeProbe(run=run_omp, relays=False),
}
