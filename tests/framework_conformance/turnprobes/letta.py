"""Turn-outcome probes for the letta adapter(s); see ``turnprobes``."""

from __future__ import annotations

from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.turnprobes import (
    ROOM_ID,
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
)


async def run_letta_turn(script: TurnScript, tools: FakeAgentTools) -> None:
    from tests.adapters.lettakit import (  # noqa: PLC0415 -- letta extra, absent from some lane venvs
        ready_letta_adapter,
        scripted_letta_turn,
    )

    adapter, client = ready_letta_adapter(room_id=ROOM_ID)
    client.agents.messages.create.side_effect = scripted_letta_turn(
        adapter,
        room_id=ROOM_ID,
        tool_calls=[(call.name, call.arguments) for call in script.tool_calls],
        final_text=script.final_text,
    )
    await adapter.on_event(turn_input(tools))


PROBES: dict[str, TurnOutcomeProbe] = {"letta": TurnOutcomeProbe(run=run_letta_turn)}
