"""Adapters using the shared cancellation owner stop their backend turns."""

from __future__ import annotations

import pytest

from band.core.simple_adapter import SimpleAdapter
from tests.framework_conformance.outcomeprobes import band_adapters
from tests.framework_conformance.stopprobes import StopCase
from tests.framework_conformance.stopprobes import a2a as a2a_probe
from tests.framework_conformance.stopprobes import acp as acp_probe
from tests.framework_conformance.stopprobes import claudesdk as claudesdk_probe
from tests.framework_conformance.stopprobes import codex as codex_probe
from tests.framework_conformance.stopprobes import copilotsdk as copilotsdk_probe
from tests.framework_conformance.stopprobes import cursoracp as cursoracp_probe
from tests.framework_conformance.stopprobes import letta as letta_probe
from tests.framework_conformance.stopprobes import opencode as opencode_probe

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
}

#: Subclasses that keep a probed parent's ``backend_turns`` and ``on_message``,
#: so the parent's probe covers them.
INHERITED_PROBES: dict[str, str] = {
    "CopilotACPAdapter": "ACPClientAdapter",
    "OmpACPAdapter": "ACPClientAdapter",
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
}

CASES = [
    pytest.param(adapter, case, id=f"{adapter}-{name}")
    for adapter, cases in STOP_PROBES.items()
    for name, case in cases.items()
]


def runs_parents_stop(cls: type[SimpleAdapter], parent: type[SimpleAdapter]) -> bool:
    return (
        issubclass(cls, parent)
        and cls.on_message is parent.on_message
        and cls.backend_turns is parent.backend_turns
    )


class TestBackendStop:
    def test_every_migrated_adapter_is_probed(self) -> None:
        migrated = {
            name
            for name, cls in band_adapters().items()
            if cls.backend_turns is not SimpleAdapter.backend_turns
        }

        assert migrated - (STOP_PROBES.keys() | INHERITED_PROBES.keys()) == set()

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
