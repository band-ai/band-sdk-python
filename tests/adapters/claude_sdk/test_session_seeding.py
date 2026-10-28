"""A Claude conversation is owed the room's history until a prompt of its own
completes, and only then is it recorded for resume."""

from __future__ import annotations

import pytest

from band.adapters.claude_sdk import ROOM_MEMORY_HEADER
from band.core.protocols import TurnResultAlreadyReported
from tests.adapters.claude_sdk.fakecli import EndTurn
from tests.adapters.claude_sdk.helpers import OpenRoom, said

FACT = "the milk is in the fridge"
REMEMBERED = f"[Alice]: {FACT}"


class TestSessionSeeding:
    async def test_a_resumed_session_is_not_handed_the_history_again(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("welcome back")])

        await room.send(
            "hello again", history=(said("Alice", FACT),), session_id="sess-kept"
        )

        [prompt] = room.claude.prompts
        assert room.claude.resumed == ["sess-kept"]
        assert ROOM_MEMORY_HEADER not in prompt
        assert FACT not in prompt

    @pytest.mark.parametrize(
        ("recorded", "attempts"),
        [(None, [None]), ("sess-broken", ["sess-broken", None])],
        ids=["nothing-recorded", "resume-refused"],
    )
    async def test_a_fresh_session_is_handed_the_history(
        self,
        claude_room: OpenRoom,
        recorded: str | None,
        attempts: list[str | None],
    ) -> None:
        room = await claude_room()
        room.claude.unresumable.add("sess-broken")
        room.claude.script([room.model_reply("hi")])

        await room.send("hello", history=(said("Alice", FACT),), session_id=recorded)

        [prompt] = room.claude.prompts
        assert room.claude.resumed == attempts
        assert prompt.startswith(f"{ROOM_MEMORY_HEADER}\n{REMEMBERED}\n\n")

    async def test_an_errored_seed_is_not_recorded_and_the_next_turn_seeds_again(
        self, claude_room: OpenRoom
    ) -> None:
        """The next turn is no bootstrap, so the history it replays is fetched
        from the room; once it completes, the session is recorded and no
        later turn replays the history."""
        room = await claude_room()
        room.tools.set_room_context(
            [
                {
                    "id": "earlier",
                    "message_type": "text",
                    "sender_id": "u1",
                    "sender_type": "User",
                    "sender_name": "Alice",
                    "content": FACT,
                }
            ]
        )
        room.claude.script(
            [EndTurn(is_error=True, result="overloaded")],
            [room.model_reply("seeded now")],
            [room.model_reply("still here")],
        )

        with pytest.raises(TurnResultAlreadyReported):
            await room.send("hello", history=(said("Alice", FACT),))
        assert room.persisted_sessions == []

        await room.send("again")
        await room.send("and again")

        [session] = room.claude.sessions
        failed_seed, reseed, seeded = room.claude.prompts
        assert ROOM_MEMORY_HEADER in failed_seed
        assert reseed.startswith(f"{ROOM_MEMORY_HEADER}\n{REMEMBERED}\n\n")
        assert ROOM_MEMORY_HEADER not in seeded
        assert room.persisted_sessions == [session.session_id]
        assert room.chat == ["seeded now", "still here"]
