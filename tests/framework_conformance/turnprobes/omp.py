"""Run OMP turn-outcome conformance through the real RPC client."""

from __future__ import annotations

import tempfile
from pathlib import Path

from pydantic import TypeAdapter

from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.ompadapter import started_omp_adapter
from tests.framework_conformance.turnprobes import (
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
)


async def run_omp(script: TurnScript, tools: FakeAgentTools) -> None:
    with tempfile.TemporaryDirectory(prefix="band-conformance-turn-") as scripts:
        script_path = Path(scripts) / "turn.json"
        script_path.write_bytes(TypeAdapter(TurnScript).dump_json(script))
        async with started_omp_adapter(
            "tests.framework_conformance.turnprobes.omppeer", str(script_path)
        ) as adapter:
            await adapter.on_event(turn_input(tools))


PROBES: dict[str, TurnOutcomeProbe] = {
    "omp": TurnOutcomeProbe(run=run_omp, relays=False),
}
