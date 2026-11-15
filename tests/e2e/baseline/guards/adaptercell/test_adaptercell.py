from __future__ import annotations

from typing import Any

from tests.e2e.baseline.guards.adaptercell.kit import (
    _cell,
)
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent


class TestAdapterCell:
    def test_build_uses_cell_default_then_override(
        self, captured: list[dict[str, Any]]
    ) -> None:
        cell = _cell(prompt="CELL")
        cell.build()  # None → cell default
        cell.build(prompt="OVERRIDE")  # explicit → override
        assert [c["prompt"] for c in captured] == ["CELL", "OVERRIDE"]

    async def test_run_as_forwards_override(
        self, captured: list[dict[str, Any]]
    ) -> None:
        cell = _cell(prompt="CELL")
        identity = ProvisionedAgent(id="i", api_key="k", name="n")
        async with cell.run_as(identity):  # cell default
            pass
        async with cell.run_as(identity, prompt="RUN"):  # override
            pass
        assert [c["prompt"] for c in captured] == ["CELL", "RUN"]

    async def test_running_forwards_override(
        self, captured: list[dict[str, Any]]
    ) -> None:
        cell = _cell(prompt="CELL")
        async with cell.running():  # cell default
            pass
        async with cell.running(prompt="RUN2"):  # override
            pass
        assert [c["prompt"] for c in captured] == ["CELL", "RUN2"]
