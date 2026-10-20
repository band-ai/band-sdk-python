from __future__ import annotations

from agno.models.message import Message
from agno.run.agent import RunOutput

from band.adapters.agno import (
    AgnoAdapter,
)


class TestPersistAndAccumulate:
    def test_persist_keeps_only_conversation_roles(self, make_agno_agent):
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent=agent)
        response = RunOutput(
            messages=[
                Message(role="system", content="instructions"),
                Message(role="user", content="hi"),
                Message(role="assistant", content="hello"),
                Message(role="developer", content="state"),
                Message(role="tool", content="result"),
            ]
        )

        adapter._persist_turn("room-1", response)

        kept = [m.role for m in adapter._message_history["room-1"]]
        assert kept == ["user", "assistant", "tool"]

    def test_bootstrap_seeds_committed_transcript_from_history(
        self, make_agno_agent, sample_platform_message
    ):
        # Bootstrap seeds the committed transcript from rehydrated history. The
        # returned run input is that seed plus this turn's live message, but
        # building it must NOT push the live message into the committed store.
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent=agent)
        seed = [Message(role="user", content="earlier")]

        run_input_msgs = adapter._build_run_input(
            sample_platform_message,
            seed,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert [m.content for m in run_input_msgs] == [
            "earlier",
            sample_platform_message.format_for_llm(),
        ]
        # Committed transcript holds only the rehydrated seed.
        assert [m.content for m in adapter._message_history["room-1"]] == ["earlier"]

    def test_build_run_input_does_not_mutate_committed_transcript(
        self, make_agno_agent, sample_platform_message
    ):
        # A non-bootstrap turn reads the committed transcript but never writes to
        # it; the store is only ever advanced by _persist_turn after a run.
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent=agent)
        adapter._message_history["room-1"] = [Message(role="user", content="committed")]

        adapter._build_run_input(
            sample_platform_message,
            [],
            "participants",
            "contacts",
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert [m.content for m in adapter._message_history["room-1"]] == ["committed"]
