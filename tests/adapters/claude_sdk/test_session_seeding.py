"""A Claude conversation is owed the room's history until a prompt of its own
completes, and only then is it recorded for resume."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from band.adapters.claude_sdk import ROOM_MEMORY_HEADER, ClaudeSDKAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.runtime.formatters import format_history_for_llm
from tests.adapters.claude_sdk.fakecli import EndTurn, Hold
from tests.adapters.claude_sdk.helpers import (
    AGENT_NAME,
    APPROVER,
    TURN_TIMEOUT_S,
    ClaudeRoom,
    OpenRoom,
    said,
)
from tests.claudesdkrecords import legacy_record, recorded_session
from tests.roomhistory import stored

FACT = "the milk is in the fridge"
REMEMBERED = f"[Alice]: {FACT}"
LATER = "a message posted after the turn"
BROKEN = "sess-broken"
MENTION_TOKEN = f"@[[{APPROVER['id']}]]"
OWN_REPLY = "noted"
# A mention as the platform stores it, and an earlier reply of the agent's own.
STORED_HISTORY = [
    stored(f"{MENTION_TOKEN} {FACT}"),
    stored(OWN_REPLY, message_id="own", sender_name=AGENT_NAME, sender_type="Agent"),
]


def memory(prompt: str) -> str:
    """The room memory that opens ``prompt``."""
    return prompt.partition("\n\n")[0]


@contextmanager
def history_unreachable(room: ClaudeRoom) -> Iterator[None]:
    """The room's history cannot be fetched while the block runs."""
    room.tools.fetch_room_context_error = ConnectionError("platform unreachable")
    try:
        yield
    finally:
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
        ("recorded", "unresumable", "attempts"),
        [(None, set(), [None]), (BROKEN, {BROKEN}, [BROKEN, None])],
        ids=["nothing-recorded", "resume-refused"],
    )
    async def test_a_fresh_session_is_handed_the_history(
        self,
        claude_room: OpenRoom,
        recorded: str | None,
        unresumable: set[str],
        attempts: list[str | None],
    ) -> None:
        room = await claude_room()
        room.claude.unresumable.update(unresumable)
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

    async def test_a_resumed_session_keeps_its_conversation_after_an_error(
        self, claude_room: OpenRoom
    ) -> None:
        """A restored session already holds the room's context, so a failed
        turn does not drop it."""
        room = await claude_room()
        room.claude.script(
            [EndTurn(is_error=True, result="overloaded")],
            [room.model_reply("still here")],
        )

        with pytest.raises(TurnResultAlreadyReported):
            await room.send(
                "hello again", history=(said("Alice", FACT),), session_id="sess-kept"
            )
        await room.send("again")

        [session] = room.claude.sessions
        assert session.alive is True
        assert room.claude.resumed == ["sess-kept"]
        assert all(ROOM_MEMORY_HEADER not in prompt for prompt in room.claude.prompts)
        assert room.chat == ["still here"]

    async def test_an_errored_seed_is_not_recorded_and_the_next_turn_seeds_again(
        self, claude_room: OpenRoom
    ) -> None:
        """The errored seed's conversation is dropped. The next turn starts a
        fresh one and, being no bootstrap, fetches the history it replays from
        the room, up to that turn's own input; once it completes, the session
        is recorded and no later turn replays the history."""
        room = await claude_room()
        again, later = room.message("again"), room.message(LATER)
        room.tools.set_room_context(
            [
                stored(FACT),
                stored(again.content, message_id=again.id, sender_name="Bob"),
                stored(later.content, message_id=later.id),
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

        await room.deliver(again)
        await room.send("and again")

        dropped, session = room.claude.sessions
        failed_seed, reseed, seeded = room.claude.prompts
        assert room.claude.resumed == [None, None]
        assert dropped.alive is False
        assert ROOM_MEMORY_HEADER in failed_seed
        assert reseed.startswith(f"{ROOM_MEMORY_HEADER}\n{REMEMBERED}\n\n")
        assert LATER not in reseed
        assert ROOM_MEMORY_HEADER not in seeded
        assert room.persisted_sessions == [session.session_id]
        assert room.chat == ["seeded now", "still here"]

    async def test_a_reseed_without_the_history_is_not_recorded(
        self, claude_room: OpenRoom
    ) -> None:
        """A turn whose history could not be fetched still answers, but leaves
        its conversation unseeded: it is dropped, and a later turn's fresh
        session carries the history."""
        room = await claude_room()
        room.claude.script(
            [EndTurn(is_error=True, result="overloaded")],
            [room.model_reply("answered without memory")],
            [room.model_reply("seeded now")],
        )

        room.tools.set_room_context([stored(FACT)])

        with pytest.raises(TurnResultAlreadyReported):
            await room.send("hello", history=(said("Alice", FACT),))
        with history_unreachable(room):
            await room.send("again")
        assert room.persisted_sessions == []
        await room.send("and again")

        *dropped, session = room.claude.sessions
        _, unseeded, seeded = room.claude.prompts
        assert room.claude.resumed == [None, None, None]
        assert [cli.alive for cli in dropped] == [False, False]
        assert ROOM_MEMORY_HEADER not in unseeded
        assert seeded.startswith(f"{ROOM_MEMORY_HEADER}\n{REMEMBERED}\n\n")
        assert room.persisted_sessions == [session.session_id]
        assert room.chat == ["answered without memory", "seeded now"]

    async def test_a_reseed_remembers_the_room_as_the_bootstrap_does(
        self, claude_room: OpenRoom
    ) -> None:
        """Both read the same stored history: mentions resolved to handles, and
        the agent's own messages left out alike."""
        room = await claude_room()
        reseeded = room.beside("room-2")
        room.claude.script(
            [room.model_reply("hi")],
            [EndTurn(is_error=True, result="overloaded")],
            [reseeded.model_reply("seeded now")],
        )
        bootstrap_history = format_history_for_llm(
            STORED_HISTORY, participants=room.tools.participants
        )
        reseeded.tools.set_room_context(STORED_HISTORY)

        await room.send("hello", history=tuple(bootstrap_history))
        with pytest.raises(TurnResultAlreadyReported):
            await reseeded.send("hello", history=tuple(bootstrap_history))
        await reseeded.send("again")

        bootstrap, _, reseed = room.claude.prompts
        assert memory(reseed) == memory(bootstrap)
        assert FACT in memory(reseed)
        assert MENTION_TOKEN not in memory(reseed)
        assert OWN_REPLY not in memory(reseed)

    @pytest.mark.looptime
    async def test_a_timed_out_seed_is_dropped_and_the_next_turn_starts_fresh(
        self, claude_room: OpenRoom
    ) -> None:
        """An interrupted first prompt is unseeded like a failed one, even when
        its CLI stops cleanly."""
        room = await claude_room(ClaudeSDKAdapterConfig(turn_timeout_s=TURN_TIMEOUT_S))
        room.tools.set_room_context([stored(FACT)])
        room.claude.script([Hold()], [room.model_reply("seeded now")])

        with pytest.raises(TurnResultAlreadyReported):
            await room.send("hello", history=(said("Alice", FACT),))
        assert room.persisted_sessions == []
        await room.send("again")

        dropped, session = room.claude.sessions
        assert room.claude.resumed == [None, None]
        assert dropped.alive is False
        assert room.claude.prompts[-1].startswith(
            f"{ROOM_MEMORY_HEADER}\n{REMEMBERED}\n\n"
        )
        assert room.persisted_sessions == [session.session_id]

    async def test_a_cancelled_seed_is_dropped_and_the_next_turn_starts_fresh(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.tools.set_room_context([stored(FACT)])
        seeding = Hold()
        room.claude.script(
            [seeding, room.model_reply("abandoned reply")],
            [room.model_reply("seeded now")],
        )

        message = asyncio.create_task(
            room.send("hello", history=(said("Alice", FACT),))
        )
        async with seeding:
            message.cancel()
            with pytest.raises(asyncio.CancelledError):
                await message
        assert room.persisted_sessions == []
        await room.send("again")

        dropped, session = room.claude.sessions
        assert room.claude.resumed == [None, None]
        assert dropped.alive is False
        assert room.claude.prompts[-1].startswith(
            f"{ROOM_MEMORY_HEADER}\n{REMEMBERED}\n\n"
        )
        assert room.persisted_sessions == [session.session_id]
        assert room.chat == ["seeded now"]
