"""Agno history/context rehydration tests.

These drive the adapter through the real ``on_event`` path so the real
``AgnoHistoryConverter`` runs, then inspect the exact ``list[Message]`` Agno
received via the faked ``agent.arun(input=...)``. Assertions are on real Agno
``Message`` objects (roles, ``tool_calls``, ``tool_call_id``, ``from_history``),
never on hardcoded prose. History is built with the real runtime formatter
``format_history_for_llm`` rather than hand-rolled converter-ready dicts.
"""

from __future__ import annotations

from agno.models.message import Message
from agno.run.agent import RunOutput

from band.runtime.formatters import format_history_for_llm
from tests.framework_configs.fixtures import TOOL_CALL_SEARCH, TOOL_RESULT_SEARCH

from .helpers import make_agent_input, platform_msg, run_input, started


class TestRehydrationPipeline:
    """Drive on_event so the real AgnoHistoryConverter runs, then inspect the
    actual run input Agno received."""

    async def test_all_message_kinds_become_the_right_messages(
        self, sample_platform_message
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
        adapter, copy = await started(RunOutput(content="ack"))

        await adapter.on_event(
            make_agent_input(sample_platform_message, raw, is_session_bootstrap=True)
        )

        msgs = run_input(copy)
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
        assert msgs[-1].content == sample_platform_message.format_for_llm()

    async def test_unsupported_kinds_are_dropped(self, sample_platform_message):
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
        adapter, copy = await started(RunOutput(content="ack"))

        await adapter.on_event(
            make_agent_input(sample_platform_message, raw, is_session_bootstrap=True)
        )

        msgs = run_input(copy)
        # Only the plain text + current message survive; thought/unknown dropped.
        assert [m.content for m in msgs] == [
            "[Alice]: hello",
            sample_platform_message.format_for_llm(),
        ]

    async def test_history_is_from_history_but_current_message_is_live(
        self, sample_platform_message
    ):
        raw = format_history_for_llm(
            [platform_msg("h1", "hi", sender_name="Alice")],
            exclude_id=sample_platform_message.id,
        )
        adapter, copy = await started(RunOutput(content="ack"))

        await adapter.on_event(
            make_agent_input(sample_platform_message, raw, is_session_bootstrap=True)
        )

        msgs = run_input(copy)
        assert all(m.from_history for m in msgs[:-1])  # rehydrated context
        assert not msgs[-1].from_history  # the message to actually answer

    async def test_participants_and_contacts_injected_before_current_message(
        self, sample_platform_message
    ):
        adapter, copy = await started(RunOutput(content="ok"))

        await adapter.on_event(
            make_agent_input(
                sample_platform_message,
                [],
                is_session_bootstrap=True,
                participants_msg="Alice and Bob are here",
                contacts_msg="Carol is now a contact",
            )
        )

        msgs = run_input(copy)
        assert [m.content for m in msgs] == [
            "[System]: Alice and Bob are here",
            "[System]: Carol is now a contact",
            sample_platform_message.format_for_llm(),
        ]


class TestUnansweredMessage:
    async def test_current_message_excluded_from_history_then_answered(
        self, sample_platform_message, tools
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

        adapter, copy = await started(RunOutput(content="here is your answer"))

        await adapter.on_event(
            make_agent_input(current, raw, is_session_bootstrap=True, tools=tools)
        )

        tools.assert_message_sent(
            content="here is your answer", mentions=[current.sender_id]
        )
        msgs = run_input(copy)
        formatted = current.format_for_llm()
        assert sum(1 for m in msgs if m.content == formatted) == 1
        assert msgs[-1].content == formatted

    async def test_answers_unanswered_message_on_restart_bootstrap(
        self, sample_platform_message, tools
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
        adapter, copy = await started(RunOutput(content="fresh answer"))

        await adapter.on_event(
            make_agent_input(
                sample_platform_message, raw, is_session_bootstrap=True, tools=tools
            )
        )

        copy.arun.assert_awaited_once()
        tools.assert_message_sent(
            content="fresh answer", mentions=[sample_platform_message.sender_id]
        )
        assert run_input(copy)[-1].content == sample_platform_message.format_for_llm()

    async def test_trailing_unanswered_user_turns_are_preserved(
        self, sample_platform_message, tools
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
        adapter, copy = await started(RunOutput(content="answering all"))

        await adapter.on_event(
            make_agent_input(
                sample_platform_message, raw, is_session_bootstrap=True, tools=tools
            )
        )

        msgs = run_input(copy)
        assert [m.role for m in msgs] == ["user", "user", "user", "user"]
        assert [m.content for m in msgs[:3]] == [
            "[Alice]: first",
            "[Bob]: second",
            "[Alice]: third",
        ]
        tools.assert_message_sent(content="answering all")


class TestMultiTurnCarryover:
    async def test_persisted_transcript_feeds_the_next_turn(
        self, sample_platform_message
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
        adapter, copy = await started(turn)

        await adapter.on_event(
            make_agent_input(sample_platform_message, [], is_session_bootstrap=True)
        )
        await adapter.on_event(
            make_agent_input(sample_platform_message, [], is_session_bootstrap=False)
        )

        msgs = run_input(copy)  # the second (follow-up) turn's input
        assert [m.content for m in msgs[:2]] == ["[Alice]: q1", "a1"]
        assert msgs[-1].content == sample_platform_message.format_for_llm()
        assert len(msgs) == 3
