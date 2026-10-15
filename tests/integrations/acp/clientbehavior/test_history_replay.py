from __future__ import annotations

import pytest
from acp import RequestError

from band.integrations.acp.client_adapter import (
    NEW_MESSAGE_MARKER_PREFIX,
)
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    live_line,
)
from tests.integrations.acp.clientbehavior.kit import (
    REPLAY_HEADER_LINE,
    closes_the_prompt,
    rehydration_history,
    replay_boundary,
)


class TestHistoryReplay:
    @pytest.mark.asyncio
    async def test_replay_injected_when_remote_session_is_gone(
        self,
    ) -> None:
        """session/load fails for the persisted id -> the transcript is replayed,
        framed as context, and the live message stays last and unduplicated."""
        agent = FakeACPAgent(supports_session_load=True).will_say("Blue.")
        history = rehydration_history(
            "[Marco]: My favorite color is blue.",
            "[Fake Agent]: Noted.",
            session="stale-session",
        )

        async with acp_adapter(agent) as session:
            await session.send(
                "What is my favorite color?", bootstrap=True, history=history
            )

        assert agent.session_load_requests == ["stale-session"], (
            "the persisted session must be tried before any fallback"
        )
        assert len(agent.sessions) == 1, (
            "a failed load must fall back to a fresh session"
        )

        prompt = agent.prompt_texts()[0]
        assert "[Marco]: My favorite color is blue." in prompt, (
            "the room transcript was not replayed into the new session's first prompt"
        )
        assert prompt.count("What is my favorite color?") == 1, (
            "the live message must appear exactly once (replay must not duplicate it)"
        )
        assert live_line("What is my favorite color?") in prompt, (
            "the live message must carry its sender, as the transcript lines do"
        )
        assert (
            prompt.index("[System Context]")
            < prompt.index(REPLAY_HEADER_LINE)
            < prompt.index("[Marco]:")
            < replay_boundary(prompt)
        ), "prompt must read: system context, then replay, then the boundary marker"
        assert closes_the_prompt(prompt, live_line("What is my favorite color?")), (
            "the live message must come last so the model answers it, not the transcript"
        )

    @pytest.mark.asyncio
    async def test_replay_injected_when_session_load_errors(
        self,
    ) -> None:
        """A remote that errors on session/load (a protocol error, not "not found")
        must not kill the turn: the failed load counts as a miss, the transcript
        replay still fires, and the room still gets its reply."""
        agent = FakeACPAgent(supports_session_load=True).will_say("Blue.")
        agent.breaks_session_load()
        history = rehydration_history(
            "[Marco]: My favorite color is blue.", session="wedged-session"
        )

        async with acp_adapter(agent) as session:
            reply = await session.send(
                "What is my favorite color?", bootstrap=True, history=history
            )

        assert agent.session_load_requests == ["wedged-session"], (
            "the persisted session must be tried before any fallback"
        )
        assert len(agent.sessions) == 1, (
            "a load protocol error must fall back to a fresh session, not kill the turn"
        )
        assert REPLAY_HEADER_LINE in agent.prompt_texts()[0], (
            "an erroring load counts as a miss, so the replay must still fire"
        )
        assert reply.thoughts == ["Blue."], (
            "the turn must complete despite the load error"
        )

    @pytest.mark.asyncio
    async def test_no_replay_when_remote_session_loads(
        self,
    ) -> None:
        """A restored session already holds the conversation remotely; replaying the
        transcript on top would double the history the agent sees."""
        agent = FakeACPAgent(supports_session_load=True).will_say("Blue.")
        agent.knows_session("session-1")
        history = rehydration_history(
            "[Marco]: My favorite color is blue.", session="session-1"
        )

        async with acp_adapter(agent) as session:
            await session.send(
                "What is my favorite color?", bootstrap=True, history=history
            )

        assert agent.session_load_requests == ["session-1"]
        assert agent.sessions == [], (
            "a successful load must reuse the session, not recreate it"
        )

        prompt = agent.prompt_texts()[0]
        assert REPLAY_HEADER_LINE not in prompt and "[Marco]:" not in prompt, (
            "a restored session already holds the history remotely; replaying doubles it"
        )

    @pytest.mark.asyncio
    async def test_replay_injected_on_cold_boot_without_persisted_session(
        self,
        fake_agent,
    ) -> None:
        """No session id was ever persisted (e.g. the note landed while the agent was
        down), yet the room transcript exists: replay is the only path to context."""
        fake_agent.will_say("7421.")
        history = rehydration_history("[Marco]: The deploy code is 7421.")

        async with acp_adapter(fake_agent) as session:
            await session.send(
                "What is the deploy code?", bootstrap=True, history=history
            )

        assert fake_agent.session_load_requests == [], (
            "with no persisted id there is nothing to resume; no load should be attempted"
        )
        prompt = fake_agent.prompt_texts()[0]
        assert (
            REPLAY_HEADER_LINE in prompt
            and "[Marco]: The deploy code is 7421." in prompt
        ), "with no session to restore, the transcript replay is the only context path"

    @pytest.mark.asyncio
    async def test_bootstrap_with_empty_history_has_no_replay_block(
        self, fake_agent
    ) -> None:
        """A genuinely new room must not get an empty history frame."""
        fake_agent.will_say("Hello!")

        async with acp_adapter(fake_agent) as session:
            await session.send("hi", bootstrap=True)

        prompt = fake_agent.prompt_texts()[0]
        assert (
            REPLAY_HEADER_LINE not in prompt and NEW_MESSAGE_MARKER_PREFIX not in prompt
        ), "an empty history must not produce an empty replay frame or a stray boundary"

    @pytest.mark.asyncio
    async def test_replay_happens_once_not_on_later_turns(self, fake_agent) -> None:
        """The transcript is seeded into the session's first prompt only; repeating
        it on every turn would compound the duplication it exists to avoid."""
        fake_agent.will_say("ok")
        history = rehydration_history("[Marco]: My favorite color is blue.")

        async with acp_adapter(fake_agent) as session:
            await session.send("first question", bootstrap=True, history=history)
            await session.send("second question", history=history)

        first, second = fake_agent.prompt_texts()
        assert REPLAY_HEADER_LINE in first
        assert (
            REPLAY_HEADER_LINE not in second
            and NEW_MESSAGE_MARKER_PREFIX not in second
            and "[Marco]:" not in second
        ), "replay is seeded once per session; repeating it compounds duplication"

    @pytest.mark.asyncio
    async def test_replay_after_midrun_respawn(
        self,
    ) -> None:
        """A prompt failure tears the runtime down and wipes the session mappings;
        the next turn's freshly created session must be re-seeded from the room
        transcript (re-fetched, since the runtime only hands history to bootstrap
        turns), not start amnesiac."""

        outcomes = iter(["I noted your favorite color.", "boom", "Blue."])
        agent = FakeACPAgent()

        @agent.on_prompt
        async def _script(a: FakeACPAgent, sid: str) -> None:
            step = next(outcomes)
            if step == "boom":
                raise RequestError.internal_error()
            await a.say(sid, step)

        transcript = [
            {
                "id": "m1",
                "message_type": "text",
                "sender_id": "user-marco",
                "sender_type": "User",
                "sender_name": "Marco",
                "content": "My favorite color is blue.",
            },
            {
                "id": "m2",
                "message_type": "text",
                "sender_id": "fake-agent",
                "sender_type": "Agent",
                "sender_name": "Fake Agent",
                "content": "I noted your favorite color.",
            },
        ]

        async with acp_adapter(agent) as session:
            await session.send("My favorite color is blue.", bootstrap=True)
            with pytest.raises(RequestError):
                await session.send("anything")  # prompt raises -> adapter stop()
            crashed = session.last_reply
            reply = await session.send(
                "What is my favorite color?", room_context=transcript
            )

        assert "error" in crashed.outline, "the failed turn must surface an error event"
        assert reply.thoughts == ["Blue."], "the respawned turn must complete"

        prompt = agent.prompt_texts()[-1]
        assert (
            REPLAY_HEADER_LINE in prompt
            and "[Marco]: My favorite color is blue." in prompt
        ), "a session created after a respawn must be re-seeded from the transcript"
        assert closes_the_prompt(prompt, live_line("What is my favorite color?")), (
            "the live message must come last so the model answers it, not the transcript"
        )
