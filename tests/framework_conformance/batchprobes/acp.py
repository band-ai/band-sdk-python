"""Batch-input probes for the ACP client family; see ``batchprobes``.

Each probe pairs the real adapter in process with a scripted ACP peer over real
ACP JSON-RPC and returns every prompt the peer received, so the suites assert on
what actually reaches the agent. These adapters sit outside the framework
conformance matrix, so ``ADAPTERS`` also lets the drift test find them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

from band.core.types import AgentInput
from tests.framework_conformance.batchprobes import BatchProbe, deliver

if TYPE_CHECKING:
    from band.integrations.acp.client_adapter import ACPClientAdapter

AdapterFactory = Callable[[], "ACPClientAdapter"]


def generic() -> ACPClientAdapter:
    from band.integrations.acp.client_adapter import (  # noqa: PLC0415 -- acp extra, absent from some lane venvs
        ACPClientAdapter,
        ACPClientAdapterConfig,
    )

    return ACPClientAdapter(
        ACPClientAdapterConfig(command="agent", inject_band_tools=False)
    )


def copilot() -> ACPClientAdapter:
    from band.adapters.copilot_acp import (  # noqa: PLC0415 -- acp extra, absent from some lane venvs
        CopilotACPAdapter,
        CopilotACPAdapterConfig,
    )

    return CopilotACPAdapter(CopilotACPAdapterConfig(inject_band_tools=False))


def omp() -> ACPClientAdapter:
    from band.adapters.omp_acp import (  # noqa: PLC0415 -- acp extra, absent from some lane venvs
        OmpACPAdapter,
        OmpACPAdapterConfig,
    )

    return OmpACPAdapter(OmpACPAdapterConfig(inject_band_tools=False))


ADAPTERS: dict[str, AdapterFactory] = {
    "acp": generic,
    "copilot_acp": copilot,
    "omp_acp": omp,
}


def probe_of(build: AdapterFactory) -> BatchProbe:
    async def run(inp: AgentInput, final_text: str) -> str:
        pytest.importorskip("acp")
        from tests.integrations.acp.acp_toolkit.agent import (  # noqa: PLC0415 -- acp extra, absent from some lane venvs
            FakeACPAgent,
        )
        from tests.integrations.acp.acp_toolkit.harness import (  # noqa: PLC0415 -- acp extra, absent from some lane venvs
            started_acp_adapter,
        )

        agent = FakeACPAgent().will_say(final_text)
        async with started_acp_adapter(build(), agent) as session:
            await deliver(session.adapter, inp)
        return "\n\n".join(agent.prompt_texts())

    return BatchProbe(run=run)


PROBES: dict[str, BatchProbe] = {
    framework_id: probe_of(build) for framework_id, build in ADAPTERS.items()
}
