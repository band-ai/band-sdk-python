"""The room's single reader keeps stray output out of turns and retires a CLI whose output ends."""

from __future__ import annotations

import asyncio
import logging

import pytest
from claude_agent_sdk import (
    CLIJSONDecodeError,
    ResultMessage,
)

from band.integrations.claude_sdk import session_manager
from tests.adapters.claude_sdk.fakecli import AfterResult, FakeClaude, Hold
from tests.adapters.claude_sdk.helpers import exchange
from tests.integrations.claude_sdk.sessionmanager.kit import (
    STRAY_RESULT,
    Signal,
    new_manager,
    retired,
    thinking,
    thoughts,
)


class TestRoomReader:
    async def test_one_reader_keeps_output_between_turns_out_of_the_next(
        self, claude: FakeClaude, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The CLI reports background work after a turn's result. The room's
        single reader drops what arrives while no turn listens, so it is never
        buffered for the next turn, which ends at its own result."""
        manager = new_manager()
        session = manager.room("room-a")
        finished = asyncio.Event()
        claude.script([AfterResult(STRAY_RESULT, gate=finished)], thinking("second"))
        dropped = Signal("outside any turn")
        session_logger = logging.getLogger(session_manager.__name__)
        session_logger.addHandler(dropped)
        try:
            with caplog.at_level(logging.DEBUG, logger=session_manager.__name__):
                await exchange(session, "first")
                finished.set()
                await dropped.logged()
                second = await exchange(session, "second")
        finally:
            session_logger.removeHandler(dropped)
            await manager.stop()

        assert thoughts(second) == ["second"]
        assert [m.session_id for m in second if isinstance(m, ResultMessage)] == [
            "sess-1"
        ]
        assert len(claude.sessions) == 1

    async def test_a_cli_that_exits_between_turns_is_retired_by_its_reader(
        self,
        claude: FakeClaude,
    ) -> None:
        """No turn is listening when the output ends, so the reader itself hands
        the crashed CLI to the room's owner, which closes it; the next message
        starts a new conversation, as a crash noticed inside a turn does."""
        manager = new_manager()
        session = manager.room("room-a")
        session.adopt("sess-1")
        claude.script(thinking("hi"), thinking("again"))
        await exchange(session, "hi")
        closing = claude.closing = Hold()

        crashed, *_ = claude.sessions
        crashed.die()
        async with closing:
            retired = session.speaks_for(1)
        await exchange(session, "again")
        await manager.stop()

        assert retired is False
        assert claude.resumed == ["sess-1", None]

    async def test_a_read_failure_after_a_result_retires_the_cli(
        self,
        claude: FakeClaude,
    ) -> None:
        """The SDK ends its message stream on a malformed line while the CLI runs
        on. The turn already has its result and reads no further, so the reader
        retires the CLI instead of leaving the next turn on a stream nobody reads;
        the process did not crash, so the next CLI resumes the conversation."""
        manager = new_manager()
        session = manager.room("room-a")
        session.adopt("sess-1")
        malformed = asyncio.Event()
        unreadable = CLIJSONDecodeError("{", ValueError("Buffer size exceeds limit"))
        claude.script(
            [*thinking("one"), AfterResult(unreadable, gate=malformed)], thinking("two")
        )
        try:
            async with session.using() as client:
                with session.listening() as stream:
                    await client.query("one")
                    [_ async for _ in stream.until_result()]
                    malformed.set()
                    await retired(session, 1)
            second = await exchange(session, "two")
        finally:
            await manager.stop()

        assert thoughts(second) == ["two"]
        assert claude.resumed == ["sess-1", "sess-1"]
