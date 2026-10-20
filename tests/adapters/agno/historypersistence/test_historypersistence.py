from __future__ import annotations

import pytest
from agno.agent import Agent as AgnoAgent
from agno.db.in_memory import InMemoryDb

from band.adapters.agno import AgnoAdapter
from band.runtime.formatters import format_history_for_llm
from tests.adapters.agno.helpers import (
    CapturingModel,
    ContactAwareTools,
    make_agent_input,
    platform_msg,
)
from tests.adapters.agno.historypersistence.kit import (
    BAND_SENTINEL,
    ROOM_ID,
    _captured,
    _platform_message,
)


class TestHistoryPersistence:
    async def test_history_survives_restart_and_is_loaded_by_agno_not_band(self):
        db = InMemoryDb()

        def build_agent(reply: str) -> AgnoAgent:
            # Same db + session_id across instances models a persistent backend that
            # outlives a single agent process.
            return AgnoAgent(
                model=CapturingModel(reply, replies=True),
                db=db,
                session_id=ROOM_ID,
                add_history_to_context=True,
                instructions="You are Bot.",
            )

        # Startup warns that Band rehydration is disabled, and flags the guard.
        # emit=(): CapturingModel's streaming hooks are inert stubs, and this test
        # only cares about history sourcing, not narration.
        adapter = AgnoAdapter(agent=build_agent("first answer"), emit=())
        with pytest.warns(UserWarning, match="manages its own conversation history"):
            await adapter.on_started("Bot", "desc")
        assert adapter._agno_manages_history is True

        # Turn 1 — Band supplies NO history (raw=[]); only the live message is sent.
        first = _platform_message("m1", "remember the code is 42")
        await adapter.on_event(
            make_agent_input(
                first, [], is_session_bootstrap=True, tools=ContactAwareTools()
            )
        )
        assert not any(
            m.from_history for m in _captured(adapter).captured_messages or []
        )

        # "Reset": a brand-new adapter/agent instance pointed at the same db+session.
        adapter2 = AgnoAdapter(agent=build_agent("second answer"), emit=())
        with pytest.warns(UserWarning, match="manages its own conversation history"):
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
                second, band_raw, is_session_bootstrap=True, tools=ContactAwareTools()
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
