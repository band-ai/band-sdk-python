"""Guard for Agno-managed history.

When the developer's Agno agent persists and replays its own history
(``add_history_to_context=True`` *with* a ``db``), Band must stop rehydrating
its transcript into the run input — otherwise the two history sources collide
and contaminate the context. Band still keeps its per-turn transcript store; it
simply no longer feeds it back into the run.

These drive the real ``on_event`` -> ``AgnoHistoryConverter`` path and inspect
the exact ``list[Message]`` Agno received via the faked ``agent.arun(input=...)``.
"""

from __future__ import annotations

import warnings

import pytest
from agno.models.message import Message
from agno.run.agent import RunOutput

from band.adapters.agno import AgnoAdapter
from band.runtime.formatters import format_history_for_llm

from tests.adapters.agno.helpers import make_agent_input, platform_msg, run_input


class TestDetection:
    async def test_warns_and_flags_when_db_and_history_enabled(self, make_agno_agent):
        agent = make_agno_agent(add_history_to_context=True, db=object())
        adapter = AgnoAdapter(agent)

        # Detection runs against the runtime agent at startup, not in __init__.
        with pytest.warns(UserWarning, match="manages its own conversation history"):
            await adapter.on_started("TestBot", "desc")

        assert adapter._agno_manages_history is True

    @pytest.mark.parametrize(
        ("add_history_to_context", "db"),
        [
            (True, None),  # history flag but no db -> Agno loads nothing
            (False, object()),  # db but flag off
            (False, None),  # neither
        ],
    )
    async def test_no_guard_unless_both_set(
        self, make_agno_agent, add_history_to_context, db
    ):
        agent = make_agno_agent(add_history_to_context=add_history_to_context, db=db)
        adapter = AgnoAdapter(agent)

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any history warning would fail here
            await adapter.on_started("TestBot", "desc")

        assert adapter._agno_manages_history is False


class TestRehydrationDisabled:
    async def test_bootstrap_run_input_omits_rehydrated_history(
        self, make_started_adapter, sample_platform_message
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
            RunOutput(content="ack"), add_history_to_context=True, db=object()
        )

        await adapter.on_event(
            make_agent_input(
                sample_platform_message,
                raw,
                is_session_bootstrap=True,
                participants_msg="Alice and Bob are here",
            )
        )

        msgs = run_input(agent)
        # Only the participants line and the current message — no rehydrated turns.
        assert [m.content for m in msgs] == [
            "[System]: Alice and Bob are here",
            sample_platform_message.format_for_llm(),
        ]

    async def test_second_turn_does_not_carry_over_band_transcript(
        self, make_started_adapter, sample_platform_message
    ):
        turn = RunOutput(
            content="a1",
            messages=[
                Message(role="user", content="[Alice]: q1"),
                Message(role="assistant", content="a1"),
            ],
        )
        adapter, agent = await make_started_adapter(
            turn, add_history_to_context=True, db=object()
        )

        await adapter.on_event(
            make_agent_input(sample_platform_message, [], is_session_bootstrap=True)
        )
        await adapter.on_event(
            make_agent_input(sample_platform_message, [], is_session_bootstrap=False)
        )

        # The follow-up turn sends only the current message: Agno supplies prior
        # turns from its own database, so Band must not replay turn 1.
        msgs = run_input(agent)
        assert [m.content for m in msgs] == [sample_platform_message.format_for_llm()]


class TestStorePreserved:
    async def test_transcript_is_still_stored_when_guard_on(
        self, make_started_adapter, sample_platform_message
    ):
        turn = RunOutput(
            content="a1",
            messages=[
                Message(role="user", content="[Alice]: q1"),
                Message(role="assistant", content="a1"),
            ],
        )
        adapter, _ = await make_started_adapter(
            turn, add_history_to_context=True, db=object()
        )

        room_id = sample_platform_message.room_id
        await adapter.on_event(
            make_agent_input(sample_platform_message, [], is_session_bootstrap=True)
        )

        # "Store the history, just don't rehydrate it": _persist_turn still records
        # the transcript even though it is no longer fed back into the run input.
        assert adapter._message_history[room_id] == turn.messages
