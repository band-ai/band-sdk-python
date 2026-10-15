from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from tests.e2e.baseline.toolkit import provisioning
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture every ``build_adapter(...)`` call's steering; stub ``running_agent`` to no-op."""
    calls: list[dict[str, Any]] = []

    def fake_build_adapter(
        adapter_id: str, settings: Any, *, prompt: Any, features: Any, tools: Any
    ) -> object:
        calls.append({"prompt": prompt, "features": features, "tools": tools})
        return object()  # stand-in adapter; never run

    @asynccontextmanager
    async def fake_running_agent(
        provisioned: ProvisionedAgent,
        adapter: Any,
        settings: Any,
        session_config: Any = None,
    ) -> Any:
        yield provisioned

    # provisioning.py imports build_adapter at top level, so it must be patched at that
    # use site -- patching toolkit.adapters.build_adapter leaves provisioning's own
    # already-bound name pointing at the real function.
    monkeypatch.setattr(provisioning, "build_adapter", fake_build_adapter)
    monkeypatch.setattr(provisioning, "running_agent", fake_running_agent)
    return calls
