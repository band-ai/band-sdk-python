"""Resume-record probe for the native OMP adapter through its real RPC client."""

from __future__ import annotations

from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.ompadapter import started_omp_adapter
from tests.framework_conformance.resumeprobes.probe import SeedOutcome
from tests.framework_conformance.turnprobes import turn_input
from tests.integrations.omppeers.base import PEER_SESSION_FILE


def seed_failure() -> type[BaseException]:
    """The error a failed seeding prompt raises out of the turn."""
    from band.integrations.omprpc import (  # noqa: PLC0415 -- requires the optional omp extra
        OmpPromptError,
    )

    return OmpPromptError


async def seed_omp(outcome: SeedOutcome, tools: FakeAgentTools) -> str:
    async with started_omp_adapter(
        "tests.framework_conformance.resumeprobes.omppeer", outcome, emit=()
    ) as adapter:
        await adapter.on_event(turn_input(tools))
    return PEER_SESSION_FILE
