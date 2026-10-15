"""External worker cancellation keeps Claude and runtime custody through settlement."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapterConfig
from band.core.exceptions import CleanupIncompleteError
from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.tools import AgentTools
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import REPORTING_BUDGET, ClaudeRoom
from tests.conftest import make_message_event
from tests.runtime.helpers import LifecyclePlatform, rest_client_over

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


class TestExternalCancellation:
    @pytest.mark.parametrize("cleanup", ["room", "adapter"])
    async def test_external_worker_cancel_keeps_handler_and_owner_until_resolver_settles(
        self, claude_room: OpenRoom, monkeypatch: pytest.MonkeyPatch, cleanup: str
    ) -> None:
        resolving, settling = Hold(), Hold()
        native_constructions: list[tuple[Any, ...]] = []

        async def resolver(workspace: str) -> Mapping[str, Any]:
            resolving.reached.set()
            try:
                await resolving.released.wait()
            except asyncio.CancelledError:
                settling.reached.set()
                await settling.released.wait()
                raise
            return {}

        def refuse_native_client(*args: Any, **kwargs: Any) -> None:
            native_constructions.append(args)
            raise AssertionError(
                "retired resolution must not construct a native client"
            )

        monkeypatch.setattr(
            "band.integrations.claude_sdk.session_manager.ClaudeSDKClient",
            refuse_native_client,
        )
        room = await claude_room(
            ClaudeSDKAdapterConfig(retention=REPORTING_BUDGET, turn_settle_timeout_s=0),
            resolve_mcp_servers=resolver,
        )
        manager = room.adapter._session_manager
        assert manager is not None
        peer = LifecyclePlatform()
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id="agent-123", api_key="test")
            link.rest = rest

            async def handle(ctx: ExecutionContext, event: Any) -> None:
                await room.adapter.on_event(
                    AgentInput(
                        msg=PlatformMessage(
                            id=event.payload.id,
                            room_id=room.room_id,
                            content="start",
                            sender_id="user-456",
                            sender_type="User",
                            sender_name="User",
                            message_type="text",
                            metadata={},
                            created_at=datetime.now(UTC),
                        ),
                        tools=AgentTools.from_context(ctx),
                        history=HistoryProvider(raw=[]),
                        participants_msg=None,
                        contacts_msg=None,
                        is_session_bootstrap=True,
                        room_id=room.room_id,
                    )
                )

            ctx = ExecutionContext(room.room_id, link, handle)
            scope = await ctx._begin_scope()
            assert scope is not None
            running = asyncio.create_task(
                ctx._run_cycle(scope, make_message_event(room_id=room.room_id), None)
            )
            cleaning: asyncio.Task[None] | None = None
            try:
                await asyncio.wait_for(resolving.reached.wait(), timeout=5.0)
                owner = room.adapter._turn_tasks[room.room_id]
                handler = scope.handler
                assert handler is not None and owner in scope.workers
                owner.task.cancel()
                await asyncio.wait_for(settling.reached.wait(), timeout=5.0)
                cleaning = asyncio.create_task(
                    room.leave() if cleanup == "room" else room.adapter.cleanup_all()
                )
                await asyncio.wait({cleaning}, timeout=2 * CYCLE_CANCEL_GRACE_SECONDS)
                assert owner.task.cancelling() == 1
                assert room.adapter._turn_tasks.get(room.room_id) is owner
                assert owner in scope.workers and not owner.task.done()
                assert (
                    ctx._active_cycle_task is handler.task and not handler.task.done()
                )
                assert not scope.settled
                assert manager._rooms
                assert not settling.released.is_set()
                assert native_constructions == []
                assert room.claude.sessions == []
                assert cleaning.done(), "cleanup must report bounded incompleteness"
                assert isinstance(cleaning.exception(), CleanupIncompleteError)
            finally:
                resolving.released.set()
                settling.released.set()
                await asyncio.gather(running, return_exceptions=True)
                if cleaning is not None:
                    await asyncio.gather(cleaning, return_exceptions=True)
                await room.leave()
                await room.adapter.cleanup_all()

            assert owner.task.cancelling() == 1
            assert owner.task.done() and handler.task.done()
            assert scope.settled and ctx._active_cycle_task is None
            assert ctx._detached_cycle_tasks == {}
            assert not manager._rooms
            assert room.adapter._turn_tasks == {}
            assert room.adapter._turn_release == {}
            assert room.adapter._room_tools == {}
            assert room.adapter._mcp_room_tools == {}
            assert room.adapter._mcp._closing is None
            assert room.adapter._mcp.current is None
            assert native_constructions == []
            assert room.claude.sessions == []
