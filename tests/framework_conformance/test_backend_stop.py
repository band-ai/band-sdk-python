"""Every adapter that drives a backend stops that backend's turn when the
room's turn ends early; every other adapter states why it has none."""

from __future__ import annotations

import pytest

import band.adapters
from band.core.simple_adapter import SimpleAdapter
from tests.framework_conformance.stopprobes import NoBackendTurn, StopCase
from tests.framework_conformance.stopprobes import a2a as a2a_probe
from tests.framework_conformance.stopprobes import acp as acp_probe
from tests.framework_conformance.stopprobes import claudesdk as claudesdk_probe
from tests.framework_conformance.stopprobes import codex as codex_probe
from tests.framework_conformance.stopprobes import copilotsdk as copilotsdk_probe
from tests.framework_conformance.stopprobes import cursoracp as cursoracp_probe
from tests.framework_conformance.stopprobes import letta as letta_probe
from tests.framework_conformance.stopprobes import opencode as opencode_probe
from tests.framework_conformance.stopprobes import parlant as parlant_probe
from tests.framework_conformance.test_turn_outcome import band_adapters

#: The stop cases of each adapter that drives a backend, by class name.
STOP_PROBES: dict[str, dict[str, StopCase]] = {
    "A2AAdapter": a2a_probe.CASES,
    "ACPClientAdapter": acp_probe.CASES,
    "ClaudeSDKAdapter": claudesdk_probe.CASES,
    "CodexAdapter": codex_probe.CASES,
    "CopilotSDKAdapter": copilotsdk_probe.CASES,
    "CursorACPAdapter": cursoracp_probe.CASES,
    "LettaAdapter": letta_probe.CASES,
    "OpencodeAdapter": opencode_probe.CASES,
    "ParlantAdapter": parlant_probe.CASES,
}

#: Subclasses that keep a probed parent's ``backend_turns`` and ``on_message``,
#: so the parent's probe covers them.
INHERITED_PROBES: dict[str, str] = {
    "CopilotACPAdapter": "ACPClientAdapter",
    "OmpACPAdapter": "ACPClientAdapter",
}

#: Adapters with no backend turn to stop, and why.
NO_BACKEND_TURN: dict[str, NoBackendTurn] = {
    "AgnoAdapter": NoBackendTurn.IN_PROCESS,
    "AnthropicAdapter": NoBackendTurn.IN_PROCESS,
    "CrewAIAdapter": NoBackendTurn.IN_PROCESS,
    "CrewAIFlowAdapter": NoBackendTurn.IN_PROCESS,
    "GeminiAdapter": NoBackendTurn.IN_PROCESS,
    "GoogleADKAdapter": NoBackendTurn.IN_PROCESS,
    "LangGraphAdapter": NoBackendTurn.IN_PROCESS,
    "NooaAdapter": NoBackendTurn.IN_PROCESS,
    "PydanticAIAdapter": NoBackendTurn.IN_PROCESS,
    "StrandsAdapter": NoBackendTurn.IN_PROCESS,
    "A2AGatewayAdapter": NoBackendTurn.GATEWAY,
    "BandACPServerAdapter": NoBackendTurn.GATEWAY,
    "SlackAdapter": NoBackendTurn.CHANNEL,
}

#: The module each probe needs beyond the dev venv's always-installed core;
#: a venv without it skips that probe's cases.
REQUIRED_MODULES: dict[str, str] = {
    "A2AAdapter": "a2a",
    "ACPClientAdapter": "acp",
    "ClaudeSDKAdapter": "claude_agent_sdk",
    "CopilotSDKAdapter": "copilot",
    "CursorACPAdapter": "acp",
    "LettaAdapter": "letta_client",
    "ParlantAdapter": "parlant.sdk",
}

CASES = [
    pytest.param(adapter, case, id=f"{adapter}-{name}")
    for adapter, cases in STOP_PROBES.items()
    for name, case in cases.items()
]


ACCOUNTED = STOP_PROBES.keys() | INHERITED_PROBES.keys() | NO_BACKEND_TURN.keys()


def runs_parents_stop(cls: type[SimpleAdapter], parent: type[SimpleAdapter]) -> bool:
    return (
        issubclass(cls, parent)
        and cls.on_message is parent.on_message
        and cls.backend_turns is parent.backend_turns
    )


class TestBackendStop:
    def test_every_band_adapter_is_probed_or_pinned(self) -> None:
        adapters = band_adapters()

        assert adapters.keys() - ACCOUNTED == set()
        assert not STOP_PROBES.keys() & NO_BACKEND_TURN.keys()

    def test_every_exported_adapter_is_probed_or_pinned_even_without_its_extra(
        self,
    ) -> None:
        exported = {name for name in band.adapters.__all__ if name.endswith("Adapter")}

        assert exported - ACCOUNTED == set()

    def test_inherited_probes_really_run_the_parents_stop(self) -> None:
        adapters = band_adapters()
        wrong = {
            name
            for name, parent in INHERITED_PROBES.items()
            if name in adapters
            and not runs_parents_stop(adapters[name], adapters[parent])
        }

        assert wrong == set()

    @pytest.mark.parametrize(("adapter", "case"), CASES)
    async def test_ending_a_turn_leaves_these_backend_cancels(
        self, adapter: str, case: StopCase
    ) -> None:
        if (module := REQUIRED_MODULES.get(adapter)) is not None:
            pytest.importorskip(module)
        assert await case.run() == case.expected
