"""A reply to an expired approval, delivered through the real room runtime."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.adapters.opencode.approvals.wording import (
    APPROVAL_NO_LONGER_PENDING_TEMPLATE,
)
from band.integrations.opencode import OpencodeSessionState
from band.platform.event import MessageEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from band.runtime.tools.agent import AgentTools
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    ASK_DEADLINE_S,
    FakeOpencodeClient,
    event_permission,
    make_platform_message,
    model_answer,
    room_turn,
    turn_finished,
)
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import message_content
from tests.runtime.helpers import (
    AGENT_ID,
    ALICE,
    PostingPlatform,
    is_message_post,
    rest_client_over,
)
from tests.runtime.scheduler.driver import attempt_delivery

ROOM_ID = "room-1"
PERMISSION_ID = "perm-1"


class RoomPosts(PostingPlatform):
    """Records the content of every message the room receives."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def answer(self, request: httpx.Request) -> httpx.Response:
        if is_message_post(request):
            self.messages.append(message_content(request))
        return super().answer(request)


class TestLateReplyThroughTheRuntime:
    @pytest.mark.looptime
    async def test_a_reply_after_the_approval_expired_is_told_it_is_gone(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        """Once the asking turn has ended, its tools can no longer post. The
        late reply is answered on its own delivery, so the notice lands and
        the room keeps handling messages."""
        client = FakeOpencodeClient(
            prompt_event_sequences=[[event_permission("sess-1", PERMISSION_ID)]],
            reply_permission_events={PERMISSION_ID: model_answer("m1", "Declined.")},
        )
        adapter = make_adapter(
            client,
            emit=(),
            config=OpencodeAdapterConfig(approval_wait_timeout_s=ASK_DEADLINE_S),
        )
        await adapter.on_started("OpenCode Agent", "A coding agent")
        platform = RoomPosts()

        async def handle(ctx: ExecutionContext, event: MessageEvent) -> None:
            await deliver_message(
                adapter,
                make_platform_message(
                    room_id=ctx.room_id,
                    sender_id=ALICE["id"],
                    content=event.payload.content,
                ),
                AgentTools.from_context(ctx),
                OpencodeSessionState(),
                None,
                None,
                is_session_bootstrap=event.payload.id == "ask",
                room_id=ctx.room_id,
            )

        async with rest_client_over(platform.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            execution = ExecutionContext(
                ROOM_ID,
                link,
                handle,
                agent_id=AGENT_ID,
                config=SessionConfig(enable_context_hydration=False),
            )
            try:
                await attempt_delivery(
                    execution,
                    make_message_event(room_id=ROOM_ID, msg_id="ask", content="run it"),
                )
                await turn_finished(await room_turn(adapter))
                assert [r["reply"] for r in client.permission_replies] == ["reject"]

                await attempt_delivery(
                    execution,
                    make_message_event(
                        room_id=ROOM_ID,
                        msg_id="late",
                        content=f"approve {PERMISSION_ID}",
                    ),
                )

                gone = APPROVAL_NO_LONGER_PENDING_TEMPLATE.format(
                    request_id=PERMISSION_ID
                )
                assert any(gone in message for message in platform.messages)
                assert len(client.permission_replies) == 1
                assert len(client.prompt_calls) == 1
            finally:
                await adapter.on_cleanup(ROOM_ID)
                await execution.stop()
