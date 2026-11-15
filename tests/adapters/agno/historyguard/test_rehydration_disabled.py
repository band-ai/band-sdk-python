from __future__ import annotations

from agno.models.message import Message
from agno.run.agent import RunOutput

from band.runtime.formatters import format_current_inputs, format_history_for_llm
from tests.adapters.agno.helpers import make_agent_input, platform_msg, run_input


class TestRehydrationDisabled:
    async def test_bootstrap_run_input_omits_rehydrated_history(
        self, make_started_adapter, sample_platform_message, tools
    ):
        raw = format_history_for_llm(
            [
                platform_msg("h1", "Prior question", sender_name="Alice"),
                platform_msg(
                    "h2", "Earlier answer", sender_type="Agent", sender_name="TestBot"
                ),
            ],
            exclude_id=sample_platform_message.id,
        )
        adapter, agent = await make_started_adapter(
            RunOutput(content="ack"),
            add_history_to_context=True,
            db=object(),
            replies_through=tools,
        )

        await adapter.on_event(
            make_agent_input(
                sample_platform_message,
                raw,
                is_session_bootstrap=True,
                participants_msg="Alice and Bob are here",
                tools=tools,
            )
        )

        msgs = run_input(agent)
        # Only the participants line and the current message — no rehydrated turns.
        assert [m.content for m in msgs] == [
            "[System]: Alice and Bob are here",
            format_current_inputs([sample_platform_message]),
        ]

    async def test_second_turn_does_not_carry_over_band_transcript(
        self, make_started_adapter, sample_platform_message, tools
    ):
        turn = RunOutput(
            content="a1",
            messages=[
                Message(role="user", content="[Alice]: q1"),
                Message(role="assistant", content="a1"),
            ],
        )
        adapter, agent = await make_started_adapter(
            turn, add_history_to_context=True, db=object(), replies_through=tools
        )

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

        # The follow-up turn sends only the current message: Agno supplies prior
        # turns from its own database, so Band must not replay turn 1.
        msgs = run_input(agent)
        assert [m.content for m in msgs] == [
            format_current_inputs([sample_platform_message])
        ]
