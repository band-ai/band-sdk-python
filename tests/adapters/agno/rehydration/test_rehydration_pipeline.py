from __future__ import annotations

from agno.run.agent import RunOutput

from band.runtime.formatters import format_current_inputs, format_history_for_llm
from tests.adapters.agno.helpers import make_agent_input, platform_msg, run_input
from tests.framework_configs.fixtures import TOOL_CALL_SEARCH, TOOL_RESULT_SEARCH


class TestRehydrationPipeline:
    """Drive on_event so the real AgnoHistoryConverter runs, then inspect the
    actual run input Agno received."""

    async def test_all_message_kinds_become_the_right_messages(
        self, make_started_adapter, sample_platform_message, tools
    ):
        # Authentic rehydration: build platform dicts and run them through the
        # real runtime formatter (which also drops the current message).
        raw = format_history_for_llm(
            [
                platform_msg("h1", "Prior question", sender_name="Alice"),
                platform_msg(
                    "h2", "Earlier answer", sender_type="Agent", sender_name="TestBot"
                ),
                platform_msg(
                    "h3",
                    TOOL_CALL_SEARCH["content"],
                    sender_type="Agent",
                    sender_name="TestBot",
                    message_type="tool_call",
                ),
                platform_msg(
                    "h4",
                    TOOL_RESULT_SEARCH["content"],
                    sender_type="Agent",
                    sender_name="TestBot",
                    message_type="tool_result",
                ),
            ],
            exclude_id=sample_platform_message.id,
        )
        adapter, agent = await make_started_adapter(
            RunOutput(content="ack"), replies_through=tools
        )

        await adapter.on_event(
            make_agent_input(
                sample_platform_message, raw, is_session_bootstrap=True, tools=tools
            )
        )

        msgs = run_input(agent)
        assert [m.role for m in msgs] == [
            "user",  # other participant text
            "assistant",  # own-agent text
            "assistant",  # tool_call batched onto an assistant message
            "tool",  # tool_result
            "user",  # the current (live) message
        ]
        assert msgs[0].content == "[Alice]: Prior question"
        assert msgs[1].content == "Earlier answer"
        assert msgs[2].tool_calls[0]["function"]["name"] == "search"
        assert msgs[3].tool_call_id == "tc_1"
        assert msgs[-1].content == format_current_inputs([sample_platform_message])

    async def test_unsupported_kinds_are_dropped(
        self, make_started_adapter, sample_platform_message, tools
    ):
        raw = format_history_for_llm(
            [
                platform_msg("h1", "hello", sender_name="Alice"),
                platform_msg(
                    "h2",
                    "thinking out loud",
                    sender_type="Agent",
                    sender_name="TestBot",
                    message_type="thought",
                ),
                platform_msg("h3", "weird", message_type="mystery"),
            ],
            exclude_id=sample_platform_message.id,
        )
        adapter, agent = await make_started_adapter(
            RunOutput(content="ack"), replies_through=tools
        )

        await adapter.on_event(
            make_agent_input(
                sample_platform_message, raw, is_session_bootstrap=True, tools=tools
            )
        )

        msgs = run_input(agent)
        # Only the plain text + current message survive; thought/unknown dropped.
        assert [m.content for m in msgs] == [
            "[Alice]: hello",
            format_current_inputs([sample_platform_message]),
        ]

    async def test_history_is_from_history_but_current_message_is_live(
        self, make_started_adapter, sample_platform_message, tools
    ):
        raw = format_history_for_llm(
            [platform_msg("h1", "hi", sender_name="Alice")],
            exclude_id=sample_platform_message.id,
        )
        adapter, agent = await make_started_adapter(
            RunOutput(content="ack"), replies_through=tools
        )

        await adapter.on_event(
            make_agent_input(
                sample_platform_message, raw, is_session_bootstrap=True, tools=tools
            )
        )

        msgs = run_input(agent)
        assert all(m.from_history for m in msgs[:-1])  # rehydrated context
        assert not msgs[-1].from_history  # the message to actually answer

    async def test_participants_and_contacts_injected_before_current_message(
        self, make_started_adapter, sample_platform_message, tools
    ):
        adapter, agent = await make_started_adapter(
            RunOutput(content="ok"), replies_through=tools
        )

        await adapter.on_event(
            make_agent_input(
                sample_platform_message,
                [],
                is_session_bootstrap=True,
                participants_msg="Alice and Bob are here",
                contacts_msg="Carol is now a contact",
                tools=tools,
            )
        )

        msgs = run_input(agent)
        assert [m.content for m in msgs] == [
            "[System]: Alice and Bob are here",
            "[System]: Carol is now a contact",
            format_current_inputs([sample_platform_message]),
        ]
