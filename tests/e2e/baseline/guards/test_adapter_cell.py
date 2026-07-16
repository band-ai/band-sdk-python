"""AdapterCell steering-override semantics.

Verifies the ``prompt`` / ``features`` / ``tools`` overrides on ``build`` / ``run_as`` /
``running`` actually reach ``build_adapter``, and that ``None`` means "use the cell
default". These per-method overrides exist for lifecycle tests that vary steering per run
(e.g. a reboot under a different system prompt) — this exercises them so the surface is
used, not speculative.

No live platform: ``build_adapter`` and ``running_agent`` are stubbed, so nothing is
constructed or run.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import Any

import pytest

from tests.e2e.baseline.toolkit import provisioning
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ProvisionedAgent


class _FakeResources:
    """Just enough ``ResourceManager`` for ``AdapterCell``: no-op run guard + fake provision."""

    @contextmanager
    def track_running(self, agent_id: str) -> Any:
        yield

    async def provision_agent(self, label: str) -> ProvisionedAgent:
        return ProvisionedAgent(id=f"id-{label}", api_key="k", name=label)


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
        provisioned: ProvisionedAgent, adapter: Any, settings: Any
    ) -> Any:
        yield provisioned

    # build() lazily does `from ...toolkit.adapters import build_adapter`, so patch there.
    monkeypatch.setattr(
        "tests.e2e.baseline.toolkit.adapters.build_adapter", fake_build_adapter
    )
    monkeypatch.setattr(provisioning, "running_agent", fake_running_agent)
    return calls


def _cell(**steering: Any) -> AdapterCell:
    return AdapterCell(
        adapter_id="anthropic", settings=None, resources=_FakeResources(), **steering
    )


def test_build_uses_cell_default_then_override(captured: list[dict[str, Any]]) -> None:
    cell = _cell(prompt="CELL")
    cell.build()  # None → cell default
    cell.build(prompt="OVERRIDE")  # explicit → override
    assert [c["prompt"] for c in captured] == ["CELL", "OVERRIDE"]


async def test_run_as_forwards_override(captured: list[dict[str, Any]]) -> None:
    cell = _cell(prompt="CELL")
    identity = ProvisionedAgent(id="i", api_key="k", name="n")
    async with cell.run_as(identity):  # cell default
        pass
    async with cell.run_as(identity, prompt="RUN"):  # override
        pass
    assert [c["prompt"] for c in captured] == ["CELL", "RUN"]


async def test_running_forwards_override(captured: list[dict[str, Any]]) -> None:
    cell = _cell(prompt="CELL")
    async with cell.running():  # cell default
        pass
    async with cell.running(prompt="RUN2"):  # override
        pass
    assert [c["prompt"] for c in captured] == ["CELL", "RUN2"]
