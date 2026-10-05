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
from enum import StrEnum

import pytest

import band
from band.core.protocols import TurnResultAlreadyReported
from band.core.simple_adapter import SimpleAdapter
from band.runtime.tools import BandTool
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from band.testing.fake_tools import FakeAgentTools, reported_failures
from tests.framework_configs.adapters import ADAPTER_CONFIGS
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
    **opencode.PROBES,
    **toolloop.PROBES,
}

# Adapters that post the model's final text when no tool replied.
RELAYING_FRAMEWORK_IDS = frozenset(
    {"codex", "copilot_sdk", "opencode", "letta", "acp", "cursor_acp"}
)

ANSWER = "The vault code is 4471-ECHO."
REPLY = ToolCall(BandTool.SEND_MESSAGE, {"content": ANSWER, "mentions": ["@alice"]})


class Verdict(StrEnum):
    COMPLETE = "complete"
    MISSING_REPLY = "missing_reply"


@dataclass(frozen=True)
class TurnResult:
    verdict: Verdict
    messages: list[str]
    failures: list[tuple[str, str]]


async def run_turn(probe: TurnOutcomeProbe, script: TurnScript) -> TurnResult:
    tools = turn_tools()
    try:
        await probe.run(script, tools)
        verdict = Verdict.COMPLETE
    except TurnResultAlreadyReported:
        verdict = Verdict.MISSING_REPLY
    return observed(tools, verdict)


def observed(tools: FakeAgentTools, verdict: Verdict) -> TurnResult:
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


def probe_params(framework_ids: frozenset[str] | None = None) -> list[str]:
    return sorted(
        name
        for name in TURN_OUTCOME_PROBES
        if framework_ids is None or name in framework_ids
    )


@pytest.mark.parametrize("framework_id", probe_params())
@pytest.mark.parametrize("script_name", sorted(COMPLETING_SCRIPTS))
async def test_a_turn_that_answered_or_worked_completes(
    framework_id: str, script_name: str
) -> None:
    result = await run_turn(
        TURN_OUTCOME_PROBES[framework_id], COMPLETING_SCRIPTS[script_name]
    )

    assert result.verdict is Verdict.COMPLETE
    assert result.failures == []


@pytest.mark.parametrize("framework_id", probe_params())
async def test_a_turn_that_did_nothing_is_reported_once(framework_id: str) -> None:
    result = await run_turn(TURN_OUTCOME_PROBES[framework_id], TurnScript())

    assert result.verdict is Verdict.MISSING_REPLY
    assert result.failures == [MISSING_REPLY_FAILURE]
    assert result.messages == []


@pytest.mark.parametrize(
    "framework_id",
    [name for name in probe_params() if TURN_OUTCOME_PROBES[name].settle],
)
async def test_an_adapter_settled_turn_completes(framework_id: str) -> None:
    settle = TURN_OUTCOME_PROBES[framework_id].settle
    assert settle is not None
    tools = turn_tools()

    await settle(tools)

    assert tools.turn.complete
    assert reported_failures(tools) == []


@pytest.mark.parametrize("framework_id", probe_params(RELAYING_FRAMEWORK_IDS))
async def test_a_relaying_adapter_posts_the_final_text_once(framework_id: str) -> None:
    result = await run_turn(
        TURN_OUTCOME_PROBES[framework_id], TurnScript(final_text=ANSWER)
    )

    assert result == TurnResult(Verdict.COMPLETE, messages=[ANSWER], failures=[])


@pytest.mark.parametrize("framework_id", probe_params(RELAYING_FRAMEWORK_IDS))
async def test_a_tool_reply_suppresses_the_closing_text(framework_id: str) -> None:
    result = await run_turn(
        TURN_OUTCOME_PROBES[framework_id],
        TurnScript((REPLY,), final_text="Anything else?"),
    )

    assert result == TurnResult(Verdict.COMPLETE, messages=[ANSWER], failures=[])


#: Registered adapters whose turns are not the model's to answer through Band tools.
UNJUDGED_FRAMEWORK_IDS = frozenset({"crewai_flow", "parlant"})


def test_only_the_declared_adapters_go_unjudged() -> None:
    unjudged = {
        cfg.framework_id
        for cfg in ADAPTER_CONFIGS
        if not cfg.adapter_factory().judges_turns
    }

    assert unjudged == UNJUDGED_FRAMEWORK_IDS


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
        except ImportError:
            continue  # an extra this venv lacks; its own lane covers it
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


def test_every_band_adapter_is_registered_probed_or_pinned() -> None:
    registered = {type(cfg.adapter_factory()).__name__ for cfg in ADAPTER_CONFIGS}
    adapters = band_adapters()
    unprobed = {
        name
        for name, cls in adapters.items()
        if name not in registered
        and probe_running(cls, adapters) not in TURN_OUTCOME_PROBES
    }

    assert unprobed == set(PINNED_OUTSIDE_CONFIGS)


def test_every_judged_adapter_has_a_probe() -> None:
    unprobed = sorted(
        cfg.framework_id
        for cfg in ADAPTER_CONFIGS
        if cfg.adapter_factory().judges_turns
        and cfg.framework_id not in TURN_OUTCOME_PROBES
    )

    assert unprobed == []
