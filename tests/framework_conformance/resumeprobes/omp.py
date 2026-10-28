"""Resume-record probe for the native OMP adapter through its real RPC client."""

from __future__ import annotations

import sys
import tempfile

import pytest

from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.resumeprobes import SeedOutcome
from tests.framework_conformance.turnprobes import turn_input
from tests.paths import REPO_ROOT


def seed_failure() -> type[BaseException]:
    """The error a failed seeding prompt raises out of the turn."""
    from band.integrations.omprpc import (  # noqa: PLC0415 -- requires the optional omp extra
        OmpPromptError,
    )

    return OmpPromptError


async def seed_omp(outcome: SeedOutcome, tools: FakeAgentTools) -> None:
    pytest.importorskip("psutil")
    from band.adapters.omp import (  # noqa: PLC0415 -- requires the optional omp extra
        OmpAdapter,
        OmpAdapterConfig,
    )

    with tempfile.TemporaryDirectory(prefix="band-conformance-omp-") as workspace:
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=workspace,
                command=(
                    sys.executable,
                    "-u",
                    "-m",
                    "tests.framework_conformance.resumeprobes.omppeer",
                    outcome,
                ),
                env={"PYTHONPATH": str(REPO_ROOT)},
            ),
            emit=(),
        )
        try:
            await adapter.on_started("Agent", "A coding agent")
            await adapter.on_event(turn_input(tools))
        finally:
            await adapter.cleanup_all()
