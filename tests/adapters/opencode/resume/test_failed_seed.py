"""A fresh OpenCode session whose seeding prompt fails is never resumed."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.converters.opencode import OPENCODE_RESUME_RECORD
from tests.adapters.opencode.helpers import (
    COMPUTE_BUDGET_S,
    PROMPT_REJECTED_STATUS,
    AnyHTTPStatusError,
    FakeOpencodeClient,
    event_session_error,
)
from tests.adapters.opencode.resume.kit import (
    ROOM_ID,
    TRANSCRIPT,
    answer,
    prompts,
    room_input,
    room_tools,
    started,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock


class TestFailedSeed:
    async def test_the_next_turn_seeds_a_new_session_and_only_it_is_recorded(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_session_error("sess-1", "provider unavailable")],
                answer("sess-2", "msg-2"),
            ]
        )
        adapter = await started(make_adapter(client, emit=()))
        failed, retried = room_tools(), room_tools()

        await adapter.on_event(room_input(failed, history=TRANSCRIPT, bootstrap=True))
        await adapter.on_event(room_input(retried, history=[], bootstrap=False))

        assert prompts(client) == [("sess-1", True), ("sess-2", True)]
        assert OPENCODE_RESUME_RECORD.candidates(failed.events_sent) == {}
        assert OPENCODE_RESUME_RECORD.candidates(retried.events_sent) == {
            ROOM_ID: "sess-2"
        }

    async def test_a_rejected_seed_is_replaced_by_a_new_session(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[answer("sess-2", "msg-2")],
            prompt_exceptions=[AnyHTTPStatusError(PROMPT_REJECTED_STATUS, "sess-1")],
        )
        adapter = await started(make_adapter(client, emit=()))
        rejected, retried = room_tools(), room_tools()

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.on_event(
                room_input(rejected, history=TRANSCRIPT, bootstrap=True)
            )
        await adapter.on_event(room_input(retried, history=[], bootstrap=False))

        assert prompts(client) == [("sess-1", True), ("sess-2", True)]
        assert OPENCODE_RESUME_RECORD.candidates(rejected.events_sent) == {}
        assert OPENCODE_RESUME_RECORD.candidates(retried.events_sent) == {
            ROOM_ID: "sess-2"
        }

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_a_timed_out_seed_is_replaced_by_a_new_session(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[[], answer("sess-2", "msg-2")]
        )
        adapter = await started(
            make_adapter(
                client,
                emit=(),
                config=OpencodeAdapterConfig(turn_timeout_s=COMPUTE_BUDGET_S),
            )
        )
        timed_out, retried = room_tools(), room_tools()

        async with (
            controlled_clock() as clock,
            background(
                adapter.on_event(
                    room_input(timed_out, history=TRANSCRIPT, bootstrap=True)
                )
            ) as turn,
        ):
            await adapter.watcher_started.wait()
            await clock.elapse(COMPUTE_BUDGET_S + 1)
            await turn
        await adapter.on_event(room_input(retried, history=[], bootstrap=False))

        assert prompts(client) == [("sess-1", True), ("sess-2", True)]
        assert OPENCODE_RESUME_RECORD.candidates(timed_out.events_sent) == {}
        assert OPENCODE_RESUME_RECORD.candidates(retried.events_sent) == {
            ROOM_ID: "sess-2"
        }

    async def test_a_failed_prompt_after_the_seed_keeps_the_session(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                answer("sess-1", "msg-1"),
                [event_session_error("sess-1", "provider unavailable")],
                answer("sess-1", "msg-3"),
            ]
        )
        adapter = await started(make_adapter(client, emit=()))

        await adapter.on_event(
            room_input(room_tools(), history=TRANSCRIPT, bootstrap=True)
        )
        await adapter.on_event(room_input(room_tools(), history=[], bootstrap=False))
        await adapter.on_event(room_input(room_tools(), history=[], bootstrap=False))

        assert prompts(client) == [
            ("sess-1", True),
            ("sess-1", False),
            ("sess-1", False),
        ]
