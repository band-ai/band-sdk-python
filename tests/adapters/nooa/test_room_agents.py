"""One NOOA agent instance per room, seeded from platform history."""

from __future__ import annotations

from band.testing import FakeAgentTools
from tests.adapters.nooa.kit import (
    declining_turns,
    prompt_text,
    room_message,
    run_turn,
    started_adapter,
)


class TestRoomAgents:
    async def test_each_room_keeps_its_own_conversation(self) -> None:
        llm = declining_turns(3)
        adapter = await started_adapter(llm)

        await run_turn(
            adapter, FakeAgentTools(), message=room_message("secret plan", room_id="a")
        )
        await run_turn(
            adapter,
            FakeAgentTools(),
            message=room_message("hello", room_id="b"),
            is_session_bootstrap=False,
        )
        room_b_prompt = prompt_text(llm)
        await run_turn(
            adapter,
            FakeAgentTools(),
            message=room_message("and again", room_id="a"),
            is_session_bootstrap=False,
        )

        assert "secret plan" not in room_b_prompt
        assert "[Alice]: secret plan" in prompt_text(llm)

    async def test_a_room_remembers_its_earlier_messages(self) -> None:
        llm = declining_turns(2)
        adapter = await started_adapter(llm)

        await run_turn(adapter, FakeAgentTools(), message=room_message("first"))
        await run_turn(
            adapter,
            FakeAgentTools(),
            message=room_message("second"),
            is_session_bootstrap=False,
        )

        assert "[Alice]: first" in prompt_text(llm)

    async def test_session_start_seeds_the_platform_transcript(self) -> None:
        llm = declining_turns(1)
        adapter = await started_adapter(llm)

        await run_turn(
            adapter, FakeAgentTools(), history=["[Bob]: the deploy is at noon"]
        )

        assert "[Bob]: the deploy is at noon" in prompt_text(llm)

    async def test_a_new_session_replaces_the_rooms_agent(self) -> None:
        llm = declining_turns(2)
        adapter = await started_adapter(llm)

        await run_turn(adapter, FakeAgentTools(), message=room_message("earlier topic"))
        await run_turn(adapter, FakeAgentTools(), message=room_message("after"))

        assert "earlier topic" not in prompt_text(llm)

    async def test_cleanup_forgets_the_room(self) -> None:
        llm = declining_turns(2)
        adapter = await started_adapter(llm)

        await run_turn(adapter, FakeAgentTools(), message=room_message("earlier topic"))
        await adapter.on_cleanup(room_message().room_id)
        await run_turn(
            adapter,
            FakeAgentTools(),
            message=room_message("after"),
            is_session_bootstrap=False,
        )

        assert "earlier topic" not in prompt_text(llm)
