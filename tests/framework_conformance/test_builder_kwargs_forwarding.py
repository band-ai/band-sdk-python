"""``builder_kwargs`` reach the adapter builder through the real dispatcher.

The live Claude settings cell and the nightly lane depend on this forwarding chain;
stubbing ``build_adapter`` (as the run-policy file must, to keep the platform out)
would let signature drift pass unnoticed. These checks register a probe spec in the
actual ``_REGISTRY`` and go through the real ``build_adapter``, so a renamed or
swallowed builder parameter fails here — offline, in the unit lane — not in the
nightly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit import adapters as adapters_module
from tests.e2e.baseline.toolkit.provisioning import AdapterCell
from tests.framework_conformance.invocationkit import CellResources


@dataclass
class ProbeCall:
    cli_settings: Any
    workspace_for_room: Any


@pytest.fixture
def probe_calls(monkeypatch: pytest.MonkeyPatch) -> list[ProbeCall]:
    """A probe adapter registered in the real registry; its builder accepts the
    adapter-specific ``cli_settings`` and the shared ``workspace_for_room``, and
    records what the real dispatcher hands it."""
    calls: list[ProbeCall] = []

    def build(
        settings: Any,
        *,
        prompt: Any,
        features: Any,
        tools: Any,
        cli_settings: str | None = None,
        workspace_for_room: Any = None,
    ) -> object:
        calls.append(
            ProbeCall(cli_settings=cli_settings, workspace_for_room=workspace_for_room)
        )
        return object()

    monkeypatch.setitem(
        adapters_module._REGISTRY,
        "probe",
        adapters_module.AdapterSpec(
            id="probe",  # type: ignore[arg-type]
            requires=(),
            supports=frozenset(),
            build=build,
        ),
    )
    return calls


def _probe_cell(**steering: Any) -> AdapterCell:
    return AdapterCell(
        adapter_id="probe", settings=None, resources=CellResources(), **steering
    )


class TestBuilderKwargsForwarding:
    def test_the_cell_default_then_a_call_override_reach_the_builder(
        self, probe_calls: list[ProbeCall]
    ) -> None:
        cell = _probe_cell(builder_kwargs={"cli_settings": "CELL"})
        cell.build()
        cell.build(cli_settings="CALL")
        assert [call.cli_settings for call in probe_calls] == ["CELL", "CALL"]

    def test_an_undeclared_builder_kwarg_fails_loud(
        self, probe_calls: list[ProbeCall]
    ) -> None:
        # The probe builder does not declare ``mystery``: swallowing it would let a
        # renamed builder parameter silently stop steering anything.
        with pytest.raises(TypeError, match="mystery"):
            _probe_cell().build(mystery=1)

    def test_workspace_for_room_still_routes_beside_the_new_kwargs(
        self, probe_calls: list[ProbeCall]
    ) -> None:
        resolver = object()
        _probe_cell(builder_kwargs={"cli_settings": "CELL"}).build(
            workspace_for_room=resolver  # type: ignore[arg-type]
        )
        (call,) = probe_calls
        assert call.workspace_for_room is resolver
        assert call.cli_settings == "CELL"

    def test_the_real_claude_builder_receives_the_selection_offline(self) -> None:
        # The nightly settings cell's whole chain, proven without the platform:
        # dispatcher -> real registered builder -> typed option on the adapter config.
        adapter = adapters_module.build_adapter(
            "claude_sdk",
            BaselineSettings(),
            prompt=None,
            features=None,
            tools=None,
            cli_settings="/operator/claude-settings.json",
        )
        assert adapter.config.cli.settings == "/operator/claude-settings.json"
