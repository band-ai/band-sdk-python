from __future__ import annotations

from agno.run.agent import RunOutput

from band.runtime.formatters import format_history_for_llm
from tests.adapters.agno.helpers import make_agent_input, platform_msg, run_input


class TestUnansweredMessage:
    async def test_current_message_excluded_from_history_then_answered(
        self, make_started_adapter, sample_platform_message, tools
    ):
        current = sample_platform_message
        # The platform context includes the current message; the formatter must
        # exclude it so it is answered, not replayed as context.
        raw = format_history_for_llm(
            [
                platform_msg("h1", "previous", sender_name="Alice"),
                {**platform_msg(current.id, current.content), "id": current.id},
            ],
            exclude_id=current.id,
        )
        assert len(raw) == 1
        assert all(current.content not in h["content"] for h in raw)

        adapter, agent = await make_started_adapter(
            RunOutput(content="here is your answer"), replies_through=tools
        )

        await adapter.on_event(
            make_agent_input(current, raw, is_session_bootstrap=True, tools=tools)
        )

        msgs = run_input(agent)
        formatted = current.format_for_llm()
        assert sum(1 for m in msgs if m.content == formatted) == 1
        assert msgs[-1].content == formatted

    async def test_answers_unanswered_message_on_restart_bootstrap(
        self, make_started_adapter, sample_platform_message, tools
    ):
        # Agent restarts: first event is bootstrap, with a completed exchange in
        # history and a brand-new unanswered question as the current message.
        raw = format_history_for_llm(
            [
                platform_msg("h1", "Earlier question", sender_name="Alice"),
                platform_msg(
                    "h2", "Earlier answer", sender_type="Agent", sender_name="TestBot"
                ),
            ],
            exclude_id=sample_platform_message.id,
        )
        adapter, agent = await make_started_adapter(
            RunOutput(content="fresh answer"), replies_through=tools
        )

        await adapter.on_event(
            make_agent_input(
                sample_platform_message, raw, is_session_bootstrap=True, tools=tools
            )
        )

        agent.arun.assert_called_once()
        assert run_input(agent)[-1].content == sample_platform_message.format_for_llm()

    async def test_trailing_unanswered_user_turns_are_preserved(
        self, make_started_adapter, sample_platform_message, tools
    ):
        # Several user turns with no assistant reply between them: agno keeps them
        # all as user messages (it does not require complete exchanges).
        raw = format_history_for_llm(
            [
                platform_msg("h1", "first", sender_name="Alice"),
                platform_msg("h2", "second", sender_name="Bob"),
                platform_msg("h3", "third", sender_name="Alice"),
            ],
            exclude_id=sample_platform_message.id,
        )
        adapter, agent = await make_started_adapter(
            RunOutput(content="answering all"), replies_through=tools
        )

        await adapter.on_event(
            make_agent_input(
                sample_platform_message, raw, is_session_bootstrap=True, tools=tools
            )
        )

        msgs = run_input(agent)
        assert [m.role for m in msgs] == ["user", "user", "user", "user"]
        assert [m.content for m in msgs[:3]] == [
            "[Alice]: first",
            "[Bob]: second",
            "[Alice]: third",
        ]
