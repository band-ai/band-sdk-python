"""Exercise the baseline builder through the released provider's HTTP tool loop."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from band.testing import FakeAgentTools
from tests.baseline.adapter import Adapter
from tests.e2e.baseline.agents import lane
from tests.e2e.baseline.settings import BaselineSettings, LLMCredentials, LLMModels
from tests.e2e.baseline.toolkit.adapters import build_adapter
from tests.e2e.baseline.toolkit.deps import Lane
from tests.e2e.baseline.toolkit.tools import ToolSpec
from tests.framework_conformance.turnprobes import turn_input


class RecordInput(BaseModel):
    """Record a note."""

    note: str


@pytest.mark.asyncio
@lane(Lane.CORE)
async def test_strands_builder_executes_a_responses_tool_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from band.testing import (  # noqa: PLC0415 -- Strands is absent from the crewai/parlant lanes
        TextTurn,
        ToolTurn,
    )
    from tests.responsespeer import (  # noqa: PLC0415 -- this HTTP peer uses Strands turn fixtures
        ResponsesPeer,
    )

    ledger = tmp_path / "notes.txt"

    @declares_turn_effect(TurnEffect.ACT)
    def record(args: BaseModel) -> str:
        assert isinstance(args, RecordInput)
        with ledger.open("a", encoding="utf-8") as notes:
            notes.write(args.note + "\n")
        return "recorded"

    tool = ToolSpec(RecordInput, record)
    peer = ResponsesPeer([ToolTurn(tool.name, {"note": "hello"}), TextTurn("")])
    monkeypatch.setattr("openai.AsyncOpenAI", peer.client)
    settings = BaselineSettings(
        llm_credentials=LLMCredentials(openai_api_key="test"),
        llm_models=LLMModels(E2E_LLM_MODEL="configured-model"),
    )
    adapter = build_adapter(Adapter.STRANDS, settings, tools=[tool])
    await adapter.on_started("Bot", "A bot")
    tools = FakeAgentTools()

    await adapter.on_event(turn_input(tools))

    assert peer.paths == ["/v1/responses", "/v1/responses"]
    assert all(request["model"] == "configured-model" for request in peer.requests)
    assert all(request["store"] is False for request in peer.requests)
    assert all("previous_response_id" not in request for request in peer.requests)
    assert peer.tool_outputs(1) == [
        {"type": "function_call_output", "call_id": peer.CALL_ID, "output": "recorded"}
    ]
    assert ledger.read_text(encoding="utf-8") == "hello\n"
    assert tools.turn.complete
