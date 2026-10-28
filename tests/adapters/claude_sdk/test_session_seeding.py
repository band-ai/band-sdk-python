"""A Claude conversation is owed the room's history until a prompt of its own
completes, and only then is it recorded for resume."""

from __future__ import annotations

import pytest

from band.adapters.claude_sdk import ROOM_MEMORY_HEADER
from band.core.protocols import TurnResultAlreadyReported
from tests.adapters.claude_sdk.fakecli import EndTurn
from tests.adapters.claude_sdk.helpers import ClaudeRoom, OpenRoom, said
from tests.converters.claude_sdk.records import legacy_record, recorded_session
from tests.roomhistory import stored

FACT = "the milk is in the fridge"
REMEMBERED = f"[Alice]: {FACT}"
LATER = "a message posted after the turn"


async def answer_without_the_history(room: ClaudeRoom) -> None:
    """Fail the room's seed (its first scripted turn errors), then answer its
    second turn while the room's history cannot be fetched."""
    room.tools.set_room_context([stored(FACT)])
    with pytest.raises(TurnResultAlreadyReported):
        await room.send("hello", history=(said("Alice", FACT),))
    room.tools.fetch_room_context_error = ConnectionError("platform unreachable")
    await room.send("again")
    room.tools.fetch_room_context_error = None


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

    @pytest.mark.parametrize(
        "record",
        [recorded_session("room-2", "sess-other"), legacy_record("sess-legacy")],
        ids=["another-rooms-record", "legacy-roomless-record"],
    )
    async def test_a_record_naming_no_session_of_this_room_starts_fresh(
        self, claude_room: OpenRoom, record: dict[str, object]
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("hi")])

        await room.send("hello", history=(said("Alice", FACT), record))

        [prompt] = room.claude.prompts
        assert room.claude.resumed == [None]
        assert prompt.startswith(f"{ROOM_MEMORY_HEADER}\n{REMEMBERED}\n\n")

    async def test_a_session_lost_to_a_crash_is_handed_the_fetched_history(
        self, claude_room: OpenRoom
    ) -> None:
        """The crash policy forgets the seeded session, so the next process
        starts a new one that is owed the room's history again."""
        room = await claude_room()
        room.tools.set_room_context([stored(FACT)])
        room.claude.script([room.model_reply("hi")], [room.model_reply("back")])

        await room.send("hello", history=(said("Alice", FACT),))
        room.claude.sessions[-1].die()
        with pytest.raises(TurnResultAlreadyReported):
            await room.send("are you there?")
        await room.send("hello again")

        assert room.claude.resumed == [None, None]
        assert room.claude.prompts[-1].startswith(
            f"{ROOM_MEMORY_HEADER}\n{REMEMBERED}\n\n"
        )

    async def test_a_session_resumed_mid_room_is_not_handed_the_history_again(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.tools.set_room_context([stored(FACT)])
        room.claude.script([room.model_reply("hi")], [room.model_reply("back")])

        await room.send("hello", history=(said("Alice", FACT),))
        await room.crash_band_server()
        await room.send("still there?")

        _, resumed_prompt = room.claude.prompts
        assert room.claude.resumed == [None, "sess-1"]
        assert ROOM_MEMORY_HEADER not in resumed_prompt

    async def test_an_errored_seed_is_not_recorded_and_the_next_turn_seeds_again(
        self, claude_room: OpenRoom
    ) -> None:
        """The next turn is no bootstrap, so the history it replays is fetched
        from the room, up to that turn's own input; once it completes, the
        session is recorded and no later turn replays the history."""
        room = await claude_room()
        room.tools.set_room_context(
            [
                stored(FACT),
                stored("again", message_id="msg-2", sender_name="Bob"),
                stored(LATER, message_id="msg-3"),
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
        assert LATER not in reseed
        assert ROOM_MEMORY_HEADER not in seeded
        assert room.persisted_sessions == [session.session_id]
        assert room.chat == ["seeded now", "still here"]

    async def test_a_reseed_without_the_history_is_not_recorded(
        self, claude_room: OpenRoom
    ) -> None:
        """A turn whose history could not be fetched still answers, but the
        session stays owed the history until a later turn carries it."""
        room = await claude_room()
        room.claude.script(
            [EndTurn(is_error=True, result="overloaded")],
            [room.model_reply("answered without memory")],
            [room.model_reply("seeded now")],
        )

        await answer_without_the_history(room)
        assert room.persisted_sessions == []
        await room.send("and again")

        [session] = room.claude.sessions
        _, unseeded, seeded = room.claude.prompts
        assert ROOM_MEMORY_HEADER not in unseeded
        assert seeded.startswith(f"{ROOM_MEMORY_HEADER}\n{REMEMBERED}\n\n")
        assert room.persisted_sessions == [session.session_id]
        assert room.chat == ["answered without memory", "seeded now"]

    async def test_a_session_left_unseeded_is_not_resumed_mid_room(
        self, claude_room: OpenRoom
    ) -> None:
        """A turn that answered without the history keeps no mapping, so a
        later restart starts fresh and hands the new session the history."""
        room = await claude_room()
        room.claude.script(
            [EndTurn(is_error=True, result="overloaded")],
            [room.model_reply("answered without memory")],
            [room.model_reply("seeded now")],
        )

        await answer_without_the_history(room)
        await room.crash_band_server()
        await room.send("still there?")

        assert room.claude.resumed == [None, None]
        assert room.claude.prompts[-1].startswith(
            f"{ROOM_MEMORY_HEADER}\n{REMEMBERED}\n\n"
        )
