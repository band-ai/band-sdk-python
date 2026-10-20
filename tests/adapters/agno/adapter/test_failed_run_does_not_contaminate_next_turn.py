from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.adapters.agno import (
    AgnoAdapter,
)
from tests.adapters.agno.adapter.kit import _msg
from tests.adapters.agno.helpers import (
    run_input,
)


class TestFailedRunDoesNotContaminateNextTurn:
    async def test_failed_turn_leaves_no_residue_in_next_run_input(
        self, make_agno_agent, tools
    ):
        # Turn 1 raises mid-run; turn 2 succeeds. The injected system/user
        # messages from the failed turn must not survive into turn 2's input.
        agent = make_agno_agent()
        succeeding = agent.arun.side_effect
        agent.arun = MagicMock(side_effect=[RuntimeError("boom"), succeeding()])
        adapter = AgnoAdapter(agent=agent, emit=())
        await adapter.on_started("TestBot", "desc")

        first = _msg("room-1", "first question", msg_id="m1")
        with pytest.raises(RuntimeError):
            await adapter.on_message(
                first,
                tools,
                [],
                "P1-participants",
                "C1-contacts",
                is_session_bootstrap=True,
                room_id="room-1",
            )

        second = _msg("room-1", "second question", msg_id="m2")
        await adapter.on_message(
            second,
            tools,
            [],
            "P2-participants",
            "C2-contacts",
            is_session_bootstrap=False,
            room_id="room-1",
        )

        contents = [m.content for m in run_input(agent)]
        # No residue from the failed turn 1.
        assert not any("P1-participants" in c for c in contents)
        assert not any("C1-contacts" in c for c in contents)
        assert first.format_for_llm() not in contents
        # Turn 2's own injected context and live message are present.
        assert any("P2-participants" in c for c in contents)
        assert contents[-1] == second.format_for_llm()
