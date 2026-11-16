"""Run OMP turn-outcome conformance through the real RPC client."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from band.runtime.custom_tools import CustomToolDef
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.ompadapter import started_omp_adapter
from tests.framework_conformance.turnprobes import (
    TurnLoop,
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
)

if TYPE_CHECKING:
    from band.adapters.omp import OmpAdapter


@asynccontextmanager
async def scripted_omp_adapter(
    script: TurnScript, *, additional_tools: list[CustomToolDef] | None = None
) -> AsyncIterator[OmpAdapter]:
    """An ``OmpAdapter`` whose native model follows ``script``."""
    with tempfile.TemporaryDirectory(prefix="band-conformance-turn-") as scripts:
        script_path = Path(scripts) / "turn.json"
        script_path.write_bytes(TypeAdapter(TurnScript).dump_json(script))
        async with started_omp_adapter(
            "tests.framework_conformance.turnprobes.omppeer",
            str(script_path),
            additional_tools=additional_tools,
        ) as adapter:
            yield adapter


async def run_omp(script: TurnScript, tools: FakeAgentTools) -> None:
    async with scripted_omp_adapter(script) as adapter:
        await adapter.on_event(turn_input(tools))


PROBES: dict[str, TurnOutcomeProbe] = {
    "omp": TurnOutcomeProbe(loop=TurnLoop.PROVIDER, run=run_omp, relays=False),
}
