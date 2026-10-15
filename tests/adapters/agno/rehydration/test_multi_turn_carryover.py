from __future__ import annotations

from agno.models.message import Message
from agno.run.agent import RunOutput

from band.runtime.formatters import format_current_inputs
from tests.adapters.agno.helpers import make_agent_input, run_input


class TestMultiTurnCarryover:
    async def test_persisted_transcript_feeds_the_next_turn(
        self, make_started_adapter, sample_platform_message, tools
    ):
        # Turn 1's run produces a transcript; _persist_turn keeps it and the next
        # turn must build on top of it (carryover through the real on_message path).
        turn = RunOutput(
            content="a1",
            messages=[
                Message(role="user", content="[Alice]: q1"),
                Message(role="assistant", content="a1"),
            ],
        )
        adapter, agent = await make_started_adapter(turn, replies_through=tools)

        await adapter.on_event(
            make_agent_input(
                sample_platform_message, [], is_session_bootstrap=True, tools=tools
            )
        )
        await adapter.on_event(
            make_agent_input(
                sample_platform_message, [], is_session_bootstrap=False, tools=tools
            )
        )

        msgs = run_input(agent)  # the second (follow-up) turn's input
        assert [m.content for m in msgs[:2]] == ["[Alice]: q1", "a1"]
        assert msgs[-1].content == format_current_inputs([sample_platform_message])
        assert len(msgs) == 3
