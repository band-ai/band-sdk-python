from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ProvisionedAgent


class FakeResources:
    """Just enough ``ResourceManager`` for ``AdapterCell``: no-op run guard + fake provision."""

    @contextmanager
    def track_running(self, agent_id: str) -> Any:
        yield

    async def provision_agent(self, label: str) -> ProvisionedAgent:
        return ProvisionedAgent(id=f"id-{label}", api_key="k", name=label)


def _cell(**steering: Any) -> AdapterCell:
    return AdapterCell(
        adapter_id="anthropic", settings=None, resources=FakeResources(), **steering
    )
