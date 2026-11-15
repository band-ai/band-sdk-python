"""Closing-room refusal preserves a real host delivery until rejoin."""

from __future__ import annotations

import asyncio

import pytest

from band.platform.link import BandLink
from band.runtime.sessionconfig import SessionConfig
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.kit import RetentionHost
from tests.adapters.lifecycle.runtime import ReplyingPlatform
from tests.adapters.roompeer import write_instruction
from tests.conftest import make_message_event
from tests.runtime.helpers import AGENT_ID, ROOM_ID, rest_client_over, run_through
from tests.runtime.scheduler.driver import attempt_delivery


class TestRuntimeClosing:
    @pytest.mark.parametrize("delivery", ["live", "backlog"])
    async def test_refused_delivery_is_replayed_once_after_confirmed_close(
        self, host: RetentionHost, delivery: str
    ) -> None:
        assert await host.send(ROOM_ID, write_instruction("before")) == ["before"]
        closing = host.children[0].closing = Hold()
        leaving = asyncio.create_task(host.leave(ROOM_ID))
        platform = ReplyingPlatform()
        content = write_instruction("after")
        message_id = "waiting-for-close"
        platform.add_message(message_id, content=content)
        async with rest_client_over(platform.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            context = run_through(
                host.adapter,
                link,
                config=SessionConfig(
                    enable_context_hydration=False, enable_working_state=False
                ),
            )
            try:
                async with closing:
                    if delivery == "live":
                        await attempt_delivery(
                            context,
                            make_message_event(
                                room_id=ROOM_ID,
                                msg_id=message_id,
                                content=content,
                                sender_id="user-1",
                            ),
                        )
                    else:
                        await context.scheduler.run_pass(recovery=True)
                    assert platform.marked("processed") == []
                    assert platform.marked("failed") == []
                    assert platform.replies == platform.errors == []
                    assert host.exited == [False]
                await leaving
                await context.scheduler.run_pass(recovery=True)
                await context.scheduler.run_pass(recovery=True)
                assert platform.marked("processed") == [message_id]
                assert platform.marked("failed") == []
                assert platform.replies == ["after"]
                assert platform.errors == []
                assert host.exited == [True, False]
                assert host.overlaps == 0
            finally:
                await context.stop()
