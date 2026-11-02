"""Confirmed turn startup and cleanup for native activity scenarios."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.integrations.opencode import OpencodeSessionState
from band.platform.event import PlatformEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from band.runtime.tools.agent import AgentTools
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.dispatch import deliver_message
from tests.adapters.hold import Hold
from tests.adapters.opencode.activity.kit import SESSION, ActivityRoom, OpenActivityRoom
from tests.adapters.opencode.activity.notices import (
    ORIGIN_CAP_SECONDS,
    ORIGIN_MESSAGE_ID,
    ConfirmationPlatform,
    NoticeRoom,
    OpenNoticeRoom,
)
from tests.adapters.opencode.helpers import (
    ASK_DEADLINE_S,
    FakeOpencodeClient,
    HeldPermissionReplyClient,
    event_session_idle,
    make_platform_message,
    room_turn,
    run_single_turn,
    turn_finished,
)
from tests.conftest import make_message_event
from tests.runtime.helpers import AGENT_ID, ALICE, rest_client_over
from tests.runtime.scheduler.driver import attempt_delivery


@pytest.fixture
def activity_room(
    make_adapter: Callable[..., OpencodeAdapter], tools: FakeAgentTools
) -> OpenActivityRoom:
    @asynccontextmanager
    async def open_room(
        client_type: type[FakeOpencodeClient] = FakeOpencodeClient,
        *,
        config: OpencodeAdapterConfig | None = None,
    ) -> AsyncIterator[ActivityRoom]:
        submitted = Hold()
        client = client_type(prompt_event_sequences=[[submitted.wait]])
        adapter = make_adapter(client, config=config, emit=())
        recorder = ActivityRecorder()
        recorder.observing(tools)
        delivery = asyncio.create_task(run_single_turn(adapter, tools))
        try:
            await submitted.arrived()
            submitted.released.set()
            turn = await room_turn(adapter)
            try:
                yield ActivityRoom(adapter, client, tools, recorder)
            finally:
                if isinstance(client, HeldPermissionReplyClient):
                    client.release_reply.set()
                await client.push_event(event_session_idle(SESSION))
                await delivery
                await turn_finished(turn)
        finally:
            submitted.released.set()
            if not delivery.done():
                delivery.cancel()
            await asyncio.gather(delivery, return_exceptions=True)

    return open_room


@pytest.fixture
def notice_room(
    make_adapter: Callable[..., OpencodeAdapter],
) -> OpenNoticeRoom:
    @asynccontextmanager
    async def open_room(
        *, reporting_cap: float | None = ORIGIN_CAP_SECONDS
    ) -> AsyncIterator[NoticeRoom]:
        peer, submitted = ConfirmationPlatform(), Hold()
        client = FakeOpencodeClient(prompt_event_sequences=[[submitted.wait]])
        adapter = make_adapter(
            client,
            emit=(),
            config=OpencodeAdapterConfig(
                approval_wait_timeout_s=ASK_DEADLINE_S,
                question_wait_timeout_s=ASK_DEADLINE_S,
            ),
        )
        await adapter.on_started("OpenCode Agent", "A coding agent")

        async def handle(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await tools.get_participants()
            await deliver_message(
                adapter,
                make_platform_message(room_id=ctx.room_id, sender_id=ALICE["id"]),
                tools,
                OpencodeSessionState(),
                None,
                None,
                is_session_bootstrap=True,
                room_id=ctx.room_id,
            )

        async with rest_client_over(peer.respond) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            execution = ExecutionContext(
                "room-1",
                link,
                handle,
                agent_id=AGENT_ID,
                config=SessionConfig(
                    enable_context_hydration=False,
                    max_working_state_seconds=reporting_cap,
                ),
            )
            delivery = asyncio.create_task(
                attempt_delivery(
                    execution,
                    make_message_event(
                        room_id=execution.room_id, msg_id=ORIGIN_MESSAGE_ID
                    ),
                )
            )
            turn = None
            try:
                await submitted.arrived()
                submitted.released.set()
                turn = await room_turn(adapter)
                assert isinstance(turn.tools, AgentTools)
                yield NoticeRoom(
                    turn.approvals, peer, execution, turn.tools, client, turn, delivery
                )
            finally:
                peer.release.set()
                submitted.released.set()
                await adapter.on_cleanup(execution.room_id)
                await asyncio.gather(delivery, return_exceptions=True)
                if turn is not None and turn.watcher is not None:
                    await asyncio.gather(turn.watcher, return_exceptions=True)
                await execution.stop()

    return open_room
