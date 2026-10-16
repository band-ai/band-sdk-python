"""Every judged adapter's turn ends with core's verdict, not its own.

Each probe (see ``turnprobes``) drives one scripted turn through the adapter's
real ``on_event``. A turn that replied, declined, did real work or was settled
by the adapter completes silently; a turn that did nothing is reported once
with core's missing-reply text and raised as ``TurnResultAlreadyReported``, so
the runtime marks it FAILED. The relaying adapters post the model's final text
unless a tool already replied; the rest discard it.

To add an adapter: write its probe in ``turnprobes/<name>.py`` and register it
in ``TURN_OUTCOME_PROBES``. ``test_every_judged_adapter_has_a_probe`` fails
when a judged adapter has none.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import dataclass

from band_sdk_core import TurnVerdict

import band
from band.core.protocols import TurnResultAlreadyReported
from band.core.simple_adapter import SimpleAdapter
from band.runtime.tools import BandTool
from band.testing import failure_reports
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.turnprobes import (
    ToolCall,
    TurnOutcomeProbe,
    TurnScript,
    acp,
    claudesdk,
    codex,
    copilotsdk,
    crewai,
    letta,
    omp,
    opencode,
    toolloop,
    turn_tools,
)

TURN_OUTCOME_PROBES: dict[str, TurnOutcomeProbe] = {
    **acp.PROBES,
    **claudesdk.PROBES,
    **codex.PROBES,
    **copilotsdk.PROBES,
    **crewai.PROBES,
    **letta.PROBES,
    **omp.PROBES,
    **opencode.PROBES,
    **toolloop.PROBES,
}

ANSWER = "The vault code is 4471-ECHO."
REPLY = ToolCall(BandTool.SEND_MESSAGE, {"content": ANSWER, "mentions": ["@alice"]})


@dataclass(frozen=True)
class TurnResult:
    verdict: TurnVerdict
    messages: list[str]
    failures: list[tuple[str, str]]


async def run_turn(probe: TurnOutcomeProbe, script: TurnScript) -> TurnResult:
    tools = turn_tools()
    try:
        await probe.run(script, tools)
        verdict = TurnVerdict.Complete
    except TurnResultAlreadyReported:
        verdict = TurnVerdict.MissingReply
    return observed(tools, verdict)


def observed(tools: FakeAgentTools, verdict: TurnVerdict) -> TurnResult:
    return TurnResult(
        verdict=verdict,
        messages=tools.chat,
        failures=failure_reports(tools),
    )


COMPLETING_SCRIPTS = {
    "decline": TurnScript((ToolCall(BandTool.NO_REPLY, {"reason": "FYI only"}),)),
    "reply": TurnScript((REPLY,)),
    "act": TurnScript((ToolCall(BandTool.CREATE_CHATROOM),)),
}


def probe_params() -> list[str]:
    return sorted(TURN_OUTCOME_PROBES)


def relaying_probe_params() -> list[str]:
    return [name for name in probe_params() if TURN_OUTCOME_PROBES[name].relays]


#: Registered adapters whose turns are not the model's to answer through Band tools.
UNJUDGED_FRAMEWORK_IDS = frozenset({"crewai_flow", "parlant"})


#: Judged adapters outside ADAPTER_CONFIGS, by the probe that runs their rows.
#: A subclass that inherits one of these ``on_message`` methods is covered too.
PROBED_OUTSIDE_CONFIGS = {"ACPClientAdapter": "acp", "CursorACPAdapter": "cursor_acp"}

#: Adapters outside ADAPTER_CONFIGS whose verdict their own tests pin.
PINNED_OUTSIDE_CONFIGS = {
    "SlackAdapter": "its brain's verdict (tests/integrations/slack)",
    "A2AAdapter": "unjudged (tests/integrations/a2a)",
    "A2AGatewayAdapter": "unjudged (tests/integrations/a2a/gateway)",
    "BandACPServerAdapter": "unjudged (tests/integrations/acp)",
}


def band_adapters() -> dict[str, type[SimpleAdapter]]:
    """Every concrete SimpleAdapter defined in band whose module imports here."""
    for module in pkgutil.walk_packages(band.__path__, "band."):
        if module.name.endswith("__main__"):
            continue
        try:
            importlib.import_module(module.name)
        except ImportError as error:
            missing = (
                error if isinstance(error, ModuleNotFoundError) else error.__cause__
            )
            if not isinstance(missing, ModuleNotFoundError) or (
                missing.name or ""
            ).startswith("band"):
                raise
            continue  # an absent optional package is covered by its owning lane
    pending, adapters = [SimpleAdapter], {}
    while pending:
        for cls in pending.pop().__subclasses__():
            pending.append(cls)
            if cls.__module__.startswith("band.") and not inspect.isabstract(cls):
                adapters[cls.__name__] = cls
    return adapters


def probe_running(
    cls: type[SimpleAdapter], adapters: dict[str, type[SimpleAdapter]]
) -> str | None:
    """The probe that runs ``cls``'s turn, via the ``on_message`` it uses."""
    for name, probe in PROBED_OUTSIDE_CONFIGS.items():
        owner = adapters.get(name)
        if owner and issubclass(cls, owner) and cls.on_message is owner.on_message:
            return probe
    return None
