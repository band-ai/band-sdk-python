"""Native completion and accepted startup own durable seed context."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from band.adapters.codex import SYSTEM_CONTEXT_PREFIX
from band.agent import Agent
from band.converters.codex import CODEX_RESUME_RECORD
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.types import CodexRequestMethod
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from band.runtime.tools.agent import AgentTools
from tests.adapters.codex.kit import (
    ALICE,
    ROOM_ID,
    agent_input,
    recorded_thread,
    replayed_history,
)
from tests.adapters.codex.seedpeer import INITIALIZE_FAILURE, native_thread_id
from tests.adapters.codex.seedtransport import PARTICIPANT_FAILURE, SeedTransport
from tests.conftest import make_message_event
from tests.roomhistory import stored
from tests.runtime.runtimecontrol.kit import never_executes


class TestSeedDelivery:
    async def test_completed_seed_records_before_reply_audience_failure(
        self, seed_transport: SeedTransport
    ) -> None:
        peer = seed_transport
        peer.participant_error = True
        adapter = await peer.start("--reply-text", "A completed native reply")
        tools = AgentTools(ROOM_ID, peer.rest, participants=[ALICE])
        with pytest.raises(httpx.ConnectError, match=PARTICIPANT_FAILURE):
            await adapter.on_event(agent_input("first", tools))

        assert CODEX_RESUME_RECORD.candidates(peer.events) == {
            ROOM_ID: native_thread_id(1)
        }
        assert ("GET", f"/api/v1/agent/chats/{ROOM_ID}/participants") in peer.requests
        assert not tools.turn.replied
        await adapter.cleanup_all()
        peer.participant_error = False
        restarted = await peer.start()
        await restarted.on_event(
            agent_input(
                "restart",
                AgentTools(ROOM_ID, peer.rest, participants=[ALICE]),
                raw_history=list(peer.events),
            )
        )
        assert len(peer.native_requests(CodexRequestMethod.THREAD_START)) == 1
        assert peer.native_requests(CodexRequestMethod.THREAD_RESUME)[0][
            "threadId"
        ] == native_thread_id(1)

    async def test_committed_context_survives_process_initialization_failure(
        self, seed_transport: SeedTransport
    ) -> None:
        peer = seed_transport
        adapter = await peer.start(
            "--fail-initialize-once", str(peer.root / "initialize-failed")
        )
        context = ExecutionContext(
            ROOM_ID,
            SimpleNamespace(rest=peer.rest),
            never_executes,
            config=SessionConfig(enable_context_hydration=False),
        )
        context.set_participants([ALICE])
        notice = "opaque contact update"
        context.inject_system_message(notice)
        first = await DefaultPreprocessor().process(
            context, make_message_event(room_id=ROOM_ID, msg_id="first"), "agent-1"
        )
        assert first is not None
        with pytest.raises(CodexJsonRpcError, match=INITIALIZE_FAILURE):
            await Agent._commit_unless_refused(first, adapter.on_event(first))
        retry = await DefaultPreprocessor().process(
            context, make_message_event(room_id=ROOM_ID, msg_id="retry"), "agent-1"
        )
        assert retry is not None
        assert retry.contacts_msg is None
        await Agent._commit_unless_refused(retry, adapter.on_event(retry))
        (prompt,) = peer.native_requests(CodexRequestMethod.TURN_START)
        assert [part["text"] for part in prompt["input"]].count(
            f"{SYSTEM_CONTEXT_PREFIX} {notice}"
        ) == 1
        assert CODEX_RESUME_RECORD.candidates(peer.events) == {
            ROOM_ID: native_thread_id(1)
        }

    async def test_unknown_native_resume_replays_into_a_fresh_thread(
        self, seed_transport: SeedTransport
    ) -> None:
        peer = seed_transport
        adapter = await peer.start()
        await adapter.on_event(
            agent_input(
                "restart",
                AgentTools(ROOM_ID, peer.rest, participants=[ALICE]),
                raw_history=[
                    recorded_thread("unknown-thread"),
                    stored("earlier context"),
                ],
            )
        )
        assert (
            peer.native_requests(CodexRequestMethod.THREAD_RESUME)[0]["threadId"]
            == "unknown-thread"
        )
        assert len(peer.native_requests(CodexRequestMethod.THREAD_START)) == 1
        (prompt,) = peer.native_requests(CodexRequestMethod.TURN_START)
        (replay,) = replayed_history(prompt)
        assert "earlier context" in replay
        assert CODEX_RESUME_RECORD.candidates(peer.events) == {
            ROOM_ID: native_thread_id(1)
        }
