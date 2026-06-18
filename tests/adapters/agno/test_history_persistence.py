"""End-to-end proof that, when Agno owns history, Agno (not Band) supplies it.

Unlike the unit tests in ``test_history_guard.py`` (which fake the Agno agent),
this drives a **real** ``AgnoAgent`` backed by a real in-memory database with a
fixed ``session_id``, mocking only the LLM via ``CapturingModel``. We run a turn,
"reset" the agent (a brand-new adapter/agent instance sharing the same db and
session), run another turn, and inspect the exact messages the model received.

Source attribution relies on two non-overlapping markers. The turn-1 message is
persisted only to Agno's db and is never handed back to Band, so if it reappears
on turn 2 it can only have come from Agno. On turn 2 Band is handed a *distinct*
sentinel history; with the guard on that sentinel must be dropped. So a pass
means Agno supplied prior context and Band's rehydration was suppressed — which
is exactly the behaviour the guard protects.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from agno.agent import Agent as AgnoAgent
from agno.db.in_memory import InMemoryDb

from band.adapters.agno import AgnoAdapter
from band.core.types import PlatformMessage
from band.runtime.formatters import format_history_for_llm

from tests.adapters.agno.helpers import (
    CapturingModel,
    SchemaTools,
    make_agent_input,
    platform_msg,
)

BAND_SENTINEL = "BAND-REHYDRATED-SENTINEL"

ROOM_ID = "room-roundtrip"


def _platform_message(msg_id: str, content: str) -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id=ROOM_ID,
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


def _captured(adapter: AgnoAdapter) -> CapturingModel:
    agent = adapter.agent
    assert agent is not None
    model = agent.model
    assert isinstance(model, CapturingModel)
    return model


async def test_history_survives_restart_and_is_loaded_by_agno_not_band():
    db = InMemoryDb()

    def build_agent(reply: str) -> AgnoAgent:
        # Same db + session_id across instances models a persistent backend that
        # outlives a single agent process.
        return AgnoAgent(
            model=CapturingModel(reply),
            db=db,
            session_id=ROOM_ID,
            add_history_to_context=True,
            instructions="You are Bot.",
        )

    # Construction warns that Band rehydration is disabled, and flags the guard.
    with pytest.warns(UserWarning, match="manages its own conversation history"):
        adapter = AgnoAdapter(build_agent("first answer"))
    assert adapter._agno_manages_history is True
    await adapter.on_started("Bot", "desc")

    # Turn 1 — Band supplies NO history (raw=[]); only the live message is sent.
    first = _platform_message("m1", "remember the code is 42")
    await adapter.on_event(
        make_agent_input(first, [], is_session_bootstrap=True, tools=SchemaTools([]))
    )
    assert not any(m.from_history for m in _captured(adapter).captured_messages or [])

    # "Reset": a brand-new adapter/agent instance pointed at the same db+session.
    with pytest.warns(UserWarning, match="manages its own conversation history"):
        adapter2 = AgnoAdapter(build_agent("second answer"))
    await adapter2.on_started("Bot", "desc")

    # Turn 2 — hand Band a DISTINCT platform history. With the guard on it must
    # be ignored; only Agno's own db history should reach the model.
    second = _platform_message("m2", "what was the code?")
    band_raw = format_history_for_llm(
        [platform_msg("hX", BAND_SENTINEL, sender_name="Ghost")],
        exclude_id=second.id,
    )
    await adapter2.on_event(
        make_agent_input(
            second, band_raw, is_session_bootstrap=True, tools=SchemaTools([])
        )
    )

    captured = _captured(adapter2).captured_messages or []
    # Band's rehydration is suppressed: its sentinel never reaches the model.
    assert not any(BAND_SENTINEL in (m.content or "") for m in captured)

    users = [m for m in captured if m.role == "user"]
    # The prior turn reappears tagged from_history -> loaded by Agno's db, not by
    # Band (whose sentinel above was dropped).
    rehydrated = [m for m in users if m.from_history]
    assert any(m.content == first.format_for_llm() for m in rehydrated)
    # The live message is the last user turn and is NOT history.
    assert users[-1].content == second.format_for_llm()
    assert users[-1].from_history is False
