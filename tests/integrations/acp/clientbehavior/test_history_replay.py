"""Fresh ACP sessions receive a framed room transcript before live input."""

from __future__ import annotations

import pytest
from acp import RequestError

from band.converters.helpers import NEW_MESSAGE_MARKER_PREFIX
from band.integrations.acp.client_adapter import SYSTEM_CONTEXT_HEADER
from band.integrations.acp.sessions import ACPSessionRestoreError
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    live_line,
)
from tests.integrations.acp.clientbehavior.kit import (
    MARCO_REPLAY_LINE,
    REPLAY_HEADER_LINE,
    UNRESTORABLE_MODEL_DETAILS,
    closes_the_prompt,
    marco_message,
    rehydration_history,
    replay_boundary,
    room_text_message,
    typescript_agent_error,
)


class TestHistoryReplay:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "missing",
        [
            RequestError.resource_not_found(),
            typescript_agent_error("ACP session not found: stale-session"),
        ],
        ids=["resource-not-found", "typescript-agent-not-found-details"],
    )
    async def test_replay_injected_when_remote_session_is_gone(
        self, missing: RequestError
    ) -> None:
        """session/load fails for the persisted id -> the transcript is replayed,
        framed as context, and the live message stays last and unduplicated."""
        agent = FakeACPAgent(supports_session_load=True).will_say("Blue.")
        agent.breaks_session_load(missing)
        history = rehydration_history(
            MARCO_REPLAY_LINE,
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
        assert MARCO_REPLAY_LINE in prompt, (
            "the room transcript was not replayed into the new session's first prompt"
        )
        assert prompt.count("What is my favorite color?") == 1, (
            "the live message must appear exactly once (replay must not duplicate it)"
        )
        assert live_line("What is my favorite color?") in prompt, (
            "the live message must carry its sender, as the transcript lines do"
        )
        assert (
            prompt.index(SYSTEM_CONTEXT_HEADER)
            < prompt.index(REPLAY_HEADER_LINE)
            < prompt.index("[Marco]:")
            < replay_boundary(prompt)
        ), "prompt must read: system context, then replay, then the boundary marker"
        assert closes_the_prompt(prompt, live_line("What is my favorite color?")), (
            "the live message must come last so the model answers it, not the transcript"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("refusal", "reason"),
        [
            (RequestError.internal_error(), "Internal error"),
            (
                typescript_agent_error(UNRESTORABLE_MODEL_DETAILS),
                f"Internal error ({UNRESTORABLE_MODEL_DETAILS})",
            ),
        ],
        ids=["internal-error", "typescript-agent-other-details"],
    )
    async def test_a_load_that_errors_is_not_replaced_on_the_same_process(
        self, refusal: RequestError, reason: str
    ) -> None:
        """A remote that errors on session/load (a protocol error, not "not found")
        may still be loading: no session is created beside it, the turn fails
        naming the agent's reason, and the next turn's session belongs to a
        fresh process."""
        agent = FakeACPAgent(supports_session_load=True).will_say("Blue.")
        agent.breaks_session_load(refusal)
        history = rehydration_history(MARCO_REPLAY_LINE, session="wedged-session")

        async with acp_adapter(agent) as session:
            with pytest.raises(ACPSessionRestoreError) as refused:
                await session.send(
                    "What is my favorite color?", bootstrap=True, history=history
                )
            assert str(refused.value) == (
                f"ACP session wedged-session could not be restored: {reason}"
            )
            failed = session.last_reply
            first_runtime = session.runtime()
            assert agent.sessions == []

            retry = await session.send("What is my favorite color?")

            assert session.runtime() is not first_runtime

        assert agent.session_load_requests == ["wedged-session"]
        assert failed.thoughts == []
        assert len(agent.sessions) == 1
        assert retry.thoughts == ["Blue."]

    @pytest.mark.asyncio
    async def test_no_replay_when_remote_session_loads(
        self,
    ) -> None:
        """A restored session already holds the conversation remotely; replaying the
        transcript on top would double the history the agent sees. It still gets
        Band's system context once, on its first prompt."""
        agent = FakeACPAgent(supports_session_load=True).will_say("Blue.")
        agent.knows_session("session-1")
        history = rehydration_history(MARCO_REPLAY_LINE, session="session-1")

        async with acp_adapter(agent) as session:
            await session.send(
                "What is my favorite color?", bootstrap=True, history=history
            )
            await session.send("And my favorite food?")

        assert agent.session_load_requests == ["session-1"]
        assert agent.sessions == [], (
            "a successful load must reuse the session, not recreate it"
        )

        first, second = agent.prompt_texts()
        assert REPLAY_HEADER_LINE not in first and "[Marco]:" not in first, (
            "a restored session already holds the history remotely; replaying doubles it"
        )
        assert SYSTEM_CONTEXT_HEADER in first, (
            "a restored session's first prompt must still carry Band's system context"
        )
        assert SYSTEM_CONTEXT_HEADER not in second, (
            "the system context is sent once per session, not on every prompt"
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
        history = rehydration_history(MARCO_REPLAY_LINE)

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
            marco_message(),
            room_text_message(
                "m2",
                sender_id="fake-agent",
                sender_type="Agent",
                sender_name="Fake Agent",
                content="I noted your favorite color.",
            ),
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
        assert REPLAY_HEADER_LINE in prompt and MARCO_REPLAY_LINE in prompt, (
            "a session created after a respawn must be re-seeded from the transcript"
        )
        assert closes_the_prompt(prompt, live_line("What is my favorite color?")), (
            "the live message must come last so the model answers it, not the transcript"
        )
