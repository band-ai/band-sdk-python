from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from band.adapters.opencode import OpencodeAdapter
from band.core.types import Emit
from band.integrations.opencode.types import OpencodeSessionState
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from band.runtime.working_state import report_tool_activity
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    event_permission,
    event_session_idle,
    event_tool_part,
    make_platform_message,
)
from tests.conftest import make_message_event
from tests.mcpbackends import backends_created_by
from tests.runtime.activityownership.kit import ActivityPlatform
from tests.runtime.helpers import ROOM_ID


class TestDetachedActivityFeature:
    @pytest.mark.looptime
    @pytest.mark.parametrize(
        "variant", ["normal", "silent", "downgrade", "cap", "disabled", "hub"]
    )
    async def test_parked_approval_owns_detail_until_native_completion(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
        httpx_mock: HTTPXMock,
        variant: str,
    ) -> None:
        activity_platform.reject_detail = variant == "downgrade"
        resumed, release = asyncio.Event(), asyncio.Event()
        captured: list[AgentTools] = []

        async def executing() -> None:
            resumed.set()
            await release.wait()

        def tool(status: str) -> dict[str, Any]:
            return event_tool_part(
                "sess-1",
                "native",
                tool="bash",
                call_id="call",
                status=status,
                input_data={"command": "private-argument"},
                output="private-result",
            )

        client = FakeOpencodeClient(
            prompt_event_sequences=[
                [tool("pending"), event_permission("sess-1", "ask")]
            ],
            reply_permission_events={
                "ask": [
                    tool("running"),
                    executing,
                    tool("completed"),
                    event_session_idle("sess-1"),
                ]
            },
        )
        adapter = OpencodeAdapter(
            client_factory=lambda _: client,
            emit=() if variant == "silent" else (Emit.TOOL_CALLS,),
        )

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            captured.append(tools)
            await adapter.on_message(
                make_platform_message(room_id=ROOM_ID, sender_id="user-456"),
                tools,
                OpencodeSessionState(),
                None,
                None,
                is_session_bootstrap=True,
                room_id=ROOM_ID,
            )

        settings = {}
        if variant == "cap":
            settings["max_working_state_seconds"] = 5.0
        if variant == "disabled":
            settings["enable_working_state"] = False
        if variant == "hub":
            settings["hub_room_id"] = ROOM_ID
        with backends_created_by():
            await adapter.on_started("Test", "Test")
            ctx = execution(handler, **settings)
            try:
                await ctx._process_event(make_message_event(msg_id="original"))
                owner = captured[0]._scope
                assert owner is not None
                workers = tuple(owner.workers)
                await ctx._process_event(make_message_event(msg_id="busy"))
                await asyncio.sleep(9.1)
                parked = list(activity_platform.activity_bodies)
                match variant:
                    case "disabled" | "hub":
                        assert not parked
                        assert not httpx_mock.get_requests()
                    case "downgrade":
                        assert parked[0].get("activity") == {"kind": "reasoning"}
                        assert len(parked[1:]) >= 3
                        assert all(body == {"working": True} for body in parked[1:])
                    case "cap":
                        assert parked[-1] == {
                            "working": True,
                            "activity": {"kind": "permission"},
                        }
                        assert len(parked) == 4
                    case _:
                        assert (
                            parked[-3:]
                            == [{"working": True, "activity": {"kind": "permission"}}]
                            * 3
                        )
                assert activity_platform.marked("processed") == ["original"]
                assert await adapter.try_handle_control(
                    make_platform_message(
                        room_id=ROOM_ID, content="approve ask", sender_id="user-456"
                    ),
                    captured[-1],
                )
                await resumed.wait()
                if variant in {"normal", "silent"}:
                    assert activity_platform.activity_bodies[-1] == {
                        "working": True,
                        "activity": {"kind": "tool_call", "name": "bash"},
                    }
                if variant == "cap":
                    assert activity_platform.activity_bodies == parked
                release.set()
                await asyncio.gather(*(worker.task for worker in workers))
                await ctx._join_activity_completion()
                if variant in {"disabled", "hub"}:
                    assert not activity_platform.activity_bodies
                    assert not httpx_mock.get_requests()
                else:
                    assert activity_platform.activity_bodies[-1] == {"working": False}
                    assert (
                        activity_platform.activity_bodies.count({"working": False}) == 1
                    )
                assert not client.aborted_sessions
                activity_platform.assert_hides("private-argument", "private-result")
                before = list(activity_platform.activity_bodies)
                await report_tool_activity(captured[0], "late-old-callback")
                assert activity_platform.activity_bodies == before
                if variant == "silent":
                    activity_platform.assert_no_tool_narration()
            finally:
                release.set()
                await adapter.on_cleanup(ROOM_ID)
