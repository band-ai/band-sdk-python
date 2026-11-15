"""Provider retirement revokes effects while the runtime settles its failure."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.adapters.codex import TRANSPORT_LOST_ERROR, CodexAdapterConfig
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.integrations.codex import TRANSPORT_CLOSED, CodexJsonRpcError, RpcEvent
from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from band.runtime.tools.agent import AgentTools
from band.runtime.tools.types import BandTool
from tests.adapters.claude_sdk.activity.kit import (
    BACKGROUND_CALL,
    BACKGROUND_TASK,
    native_task_started,
    native_tool_result,
    native_tool_uses,
)
from tests.adapters.claude_sdk.fakecli import scripted_claude
from tests.adapters.codex.activity.kit import LIST_FILES, item_started
from tests.adapters.codex.activity.nativecommands import CommandClient
from tests.adapters.codex.kit import agent_input
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.adapters.hold import Hold
from tests.baseline.decisions import ModelDecision
from tests.conftest import make_message_event
from tests.integrations.activitykit import LookupInput
from tests.platform.settlement.kit import link_over
from tests.runtime.activityownership.kit import ActivityPlatform, deliver_event
from tests.runtime.helpers import ALICE, ROOM_ID, rest_client_over, run_through
from tests.runtime.providerfaults import RpcErrorClient


class TestProviderRetirementFailure:
    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (
                CodexJsonRpcError(code=-32000, message="model unavailable"),
                "Codex JSON-RPC error -32000: model unavailable",
            ),
            (
                ConnectionError("private backend credentials"),
                GENERIC_PROVIDER_FAILURE_MESSAGE,
            ),
        ],
        ids=["curated-rpc", "private-stream-error"],
    )
    async def test_codex_retirement_preserves_the_safe_failure(
        self, error: Exception, message: str
    ) -> None:
        platform = ActivityPlatform()
        client = RpcErrorClient(error)
        adapter = make_codex_adapter(client, config=CodexAdapterConfig(), emit=())
        await adapter.on_started("Agent", "Agent")
        async with rest_client_over(platform.answer) as rest:
            ctx = run_through(
                adapter,
                link_over(rest),
                config=SessionConfig(
                    enable_context_hydration=False,
                    enable_working_state=False,
                    max_cycle_seconds=None,
                ),
            )
            ctx.set_participants([ALICE])
            try:
                await deliver_event(
                    ctx, make_message_event(room_id=ROOM_ID, msg_id="curated")
                )
                assert platform.marked("failed") == ["curated"]
                assert platform.failures == [
                    {
                        "provider": "codex",
                        "code": None,
                        "message": message,
                        "detail": None,
                    }
                ]
            finally:
                await ctx.stop()
                await adapter.cleanup_all()

    @pytest.mark.parametrize(
        ("preceding_events", "expected"),
        [
            (
                [],
                {
                    "provider": "codex",
                    "code": "transport_closed",
                    "message": TRANSPORT_LOST_ERROR,
                    "detail": None,
                },
            ),
            (
                [
                    event_notification(
                        "error",
                        {"error": {"message": "Provider failed"}, "willRetry": False},
                    )
                ],
                {
                    "provider": "codex",
                    "code": None,
                    "message": "Provider failed",
                    "detail": {
                        "codex_room_id": ROOM_ID,
                        "codex_thread_id": "thr-1",
                        "codex_turn_id": "turn-1",
                    },
                },
            ),
        ],
        ids=["transport-only", "error-before-close"],
    )
    async def test_codex_transport_close_reports_one_terminal_failure(
        self, preceding_events: list[RpcEvent], expected: dict[str, object]
    ) -> None:
        platform = ActivityPlatform()
        events = list(preceding_events)
        events.append(
            event_notification(TRANSPORT_CLOSED, {"reason": "Process exited"})
        )
        adapter = make_codex_adapter(FakeCodexClient(events=events), emit=())
        await adapter.on_started("Agent", "Agent")
        async with rest_client_over(platform.answer) as rest:
            ctx = run_through(
                adapter,
                link_over(rest),
                config=SessionConfig(
                    enable_context_hydration=False,
                    enable_working_state=False,
                    max_cycle_seconds=None,
                ),
            )
            ctx.set_participants([ALICE])
            try:
                await deliver_event(
                    ctx, make_message_event(room_id=ROOM_ID, msg_id="transport")
                )
                assert platform.marked("failed") == ["transport"]
                assert platform.failures == [expected]
            finally:
                await ctx.stop()
                await adapter.cleanup_all()

    async def test_claude_failed_stop_fails_new_delivery_before_physical_close(
        self,
    ) -> None:
        platform = ActivityPlatform()
        async with rest_client_over(platform.answer) as rest:
            with scripted_claude() as claude:
                adapter = ClaudeSDKAdapter(emit=())
                await adapter.on_started("Agent", "Agent")
                ctx = run_through(
                    adapter,
                    link_over(rest),
                    config=SessionConfig(
                        enable_context_hydration=False,
                        enable_working_state=False,
                        max_cycle_seconds=None,
                    ),
                )
                ctx.set_participants([ALICE])
                newer: asyncio.Task[None] | None = None
                foreground_hold, closing = Hold(), Hold()
                try:
                    claude.script(
                        [
                            ModelDecision.call(
                                "mcp__band__band_send_message",
                                content="Started",
                                mentions=["alice"],
                            ),
                            native_tool_uses((BACKGROUND_CALL, "Bash")),
                            native_task_started(BACKGROUND_TASK, BACKGROUND_CALL),
                            native_tool_result(BACKGROUND_CALL),
                        ]
                    )
                    await deliver_event(
                        ctx, make_message_event(room_id=ROOM_ID, msg_id="old")
                    )
                    session = adapter._session_manager.session(ROOM_ID)
                    assert session is not None and session.activity is not None
                    origin = session.activity.resolve(BACKGROUND_CALL)
                    claude.refuse_task_stop = True
                    claude.closing = closing
                    claude.script(
                        [native_tool_uses(("current", "Read")), foreground_hold]
                    )
                    newer = asyncio.create_task(
                        deliver_event(
                            ctx, make_message_event(room_id=ROOM_ID, msg_id="new")
                        )
                    )
                    await foreground_hold.arrived()
                    current_tools = adapter._room_tools[ROOM_ID]
                    origin.owner.terminate()
                    assert await claude.stop_requests.get() == BACKGROUND_TASK
                    await closing.arrived()
                    await newer
                    assert platform.marked("failed") == ["new"]
                    assert platform.marked("processed") == ["old"]
                    assert platform.failures == [
                        {
                            "provider": "claude_sdk",
                            "code": None,
                            "message": GENERIC_PROVIDER_FAILURE_MESSAGE,
                            "detail": None,
                        }
                    ]
                    assert not origin.owner.task.done()
                    with pytest.raises(asyncio.CancelledError):
                        await current_tools.send_notice("stale", mentions=["alice"])
                    assert platform.room_messages == ["Started"]
                    closing.released.set()
                    await origin.owner.task
                finally:
                    closing.released.set()
                    foreground_hold.released.set()
                    if newer is not None:
                        await asyncio.gather(newer, return_exceptions=True)
                    await ctx.stop()
                    await adapter.cleanup_all()

    async def test_codex_failed_resource_stop_revokes_a_held_foreground(self) -> None:
        platform = ActivityPlatform()
        closing, lookup = Hold(), Hold()
        client = CommandClient(
            stop_error=RuntimeError("selective stop refused"), closing=closing
        )
        client.script(
            tool_call_request(1, BandTool.NO_REPLY),
            item_started({**LIST_FILES, "processId": "42"}),
            turn_completed(),
        )
        captured: list[AgentTools] = []

        async def held_lookup(args: LookupInput) -> str:
            await lookup.wait()
            await captured[-1].send_notice("Stale callback", mentions=["alice"])
            return "done"

        adapter = make_codex_adapter(
            client, additional_tools=[(LookupInput, held_lookup)], emit=()
        )
        await adapter.on_started("Agent", "Agent")
        async with rest_client_over(platform.answer) as rest:

            async def handle(ctx: ExecutionContext, event: PlatformEvent) -> None:
                assert isinstance(event, MessageEvent)
                tools = AgentTools.from_context(ctx)
                captured.append(tools)
                await adapter.on_event(
                    agent_input(
                        event.payload.content,
                        tools,
                        is_session_bootstrap=event.payload.id == "old",
                        room_id=ROOM_ID,
                    )
                )

            ctx = ExecutionContext(
                ROOM_ID,
                link_over(rest),
                handle,
                agent_id="agent-123",
                config=SessionConfig(
                    enable_context_hydration=False,
                    enable_working_state=False,
                    max_cycle_seconds=None,
                ),
            )
            ctx.set_participants([ALICE])
            newer: asyncio.Task[None] | None = None
            try:
                await deliver_event(
                    ctx, make_message_event(room_id=ROOM_ID, msg_id="old")
                )
                room = adapter._room_clients[ROOM_ID]
                (resource,) = room.commands.values()
                assert resource.owner is not None
                client.script(tool_call_request(2, "lookup", {"note": "held"}))
                newer = asyncio.create_task(
                    deliver_event(
                        ctx, make_message_event(room_id=ROOM_ID, msg_id="new")
                    )
                )
                await lookup.arrived()
                resource.owner.terminate()
                await client.stops.get()
                with pytest.raises(asyncio.CancelledError):
                    await captured[-1].send_notice(
                        "Stale foreground", mentions=["alice"]
                    )
                assert not newer.done()
                lookup.released.set()
                await newer
                await closing.arrived()
                assert not resource.owner.task.done()
                assert platform.room_messages == []
                assert platform.marked("processed") == ["old"]
                assert platform.marked("failed") == ["new"]
                assert platform.failures == [
                    {
                        "provider": "codex",
                        "code": None,
                        "message": GENERIC_PROVIDER_FAILURE_MESSAGE,
                        "detail": None,
                    }
                ]
                closing.released.set()
                await resource.owner.task
            finally:
                lookup.released.set()
                closing.released.set()
                if newer is not None:
                    await asyncio.gather(newer, return_exceptions=True)
                await ctx.stop()
                await adapter.cleanup_all()
