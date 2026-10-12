"""Codex adapter: native callbacks reach only the Band turn that started their native turn, on the process that sent them, and a room keeps its lease through detached decisions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from band.adapters.codex import (
    TRANSPORT_LOST_ERROR,
    CodexAdapterConfig,
    CodexCommand,
    CodexContinuityError,
)
from band.core.protocols import TurnResultAlreadyReported
from band.core.retention import Residency
from band.integrations.codex import TRANSPORT_CLOSED, CodexJsonRpcError
from band.integrations.codex.types import CodexRequestMethod
from band.runtime.tools import BandTool
from band.testing import (
    events_of_type,
    reported_failures,
)
from tests.adapters.codex.kit import (
    PROCESS_EXITED,
    ROOM_ID,
    CodexRoom,
    CompletesWhileRefusingClient,
    ExitsWhileAskedClient,
    SlowTurnStartClient,
    StopsReadingAfterTurnClient,
    ToolSchemaFakeTools,
    answer_to,
    parked_on_approval,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    event_request,
    final_text,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)


class TestNativeTurnOwnership:
    """Native callbacks reach only the Band turn that started their native
    turn, on the process that sent them, and a room keeps its lease through
    detached decisions."""

    @pytest.mark.asyncio
    async def test_a_parked_decision_keeps_its_turn_leased_until_it_finishes(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            *parked_on_approval(turn_completed()), approval_wait_timeout_s=3600
        )
        await room.send("run tests")
        residency = room.adapter._room_clients[ROOM_ID].residency
        assert (residency.snapshot.leases, residency.snapshot.accepted) == (1, 1)

        await room.send(f"/{CodexCommand.APPROVE} req-10")
        await room.settled()
        assert (residency.snapshot.leases, residency.snapshot.accepted) == (0, 0)

    @pytest.mark.asyncio
    async def test_native_work_no_band_turn_started_never_gets_band_tools(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            tool_call_request(1, BandTool.NO_REPLY), turn_completed()
        )
        await room.send("hello")
        autonomous = {"threadId": "thr-1", "turnId": "autonomous"}

        room.client.emit(
            event_request(
                41,
                "item/tool/call",
                {
                    **autonomous,
                    "callId": "c-41",
                    "tool": BandTool.SEND_MESSAGE,
                    "arguments": {"content": "unasked", "mentions": ["@a"]},
                },
            ),
            event_request(
                42,
                "item/commandExecution/requestApproval",
                {**autonomous, "command": "x"},
            ),
        )

        assert (await answer_to(room.client, 41))["success"] is False
        assert await answer_to(room.client, 42) == {"decision": "decline"}
        assert [call["tool_name"] for call in room.deliveries[0].tool_calls] == [
            BandTool.NO_REPLY
        ]

    @pytest.mark.asyncio
    async def test_a_late_item_is_not_charged_to_the_next_turn(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            tool_call_request(1, BandTool.NO_REPLY),
            turn_completed("turn-1"),
            tool_call_request(2, BandTool.NO_REPLY),
            turn_completed("turn-2"),
        )
        await room.send("first")
        room.client.emit(
            event_notification(
                "item/completed",
                {
                    "threadId": "thr-1",
                    "turnId": "turn-1",
                    "item": {
                        "type": "commandExecution",
                        "id": "late",
                        "command": "make",
                        "aggregatedOutput": "late output",
                    },
                },
            )
        )

        await room.send("second")

        assert "late output" not in json.dumps(room.deliveries[-1].events_sent)

    @pytest.mark.asyncio
    async def test_events_ahead_of_the_turn_start_answer_reach_their_turn(
        self,
    ) -> None:
        client = SlowTurnStartClient(
            events=[
                final_text("on time"),
                event_request(
                    7,
                    "item/tool/call",
                    {"turnId": "someone-else", "tool": BandTool.SEND_MESSAGE},
                ),
                turn_completed(),
            ]
        )
        adapter = make_codex_adapter(client)
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Codex Agent", "A coding agent")

        await send_bootstrap(adapter, tools=tools)

        assert [e["content"] for e in events_of_type(tools, "thought")] == ["on time"]
        assert (await answer_to(client, 7))["success"] is False
        assert tools.tool_calls == []

    @pytest.mark.asyncio
    async def test_early_events_stay_ahead_of_events_sent_while_refusing_others(
        self,
    ) -> None:
        client = CompletesWhileRefusingClient(
            events=[
                event_request(
                    7,
                    "item/tool/call",
                    {"turnId": "someone-else", "tool": BandTool.SEND_MESSAGE},
                ),
                final_text("on time"),
            ]
        )
        adapter = make_codex_adapter(client)
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Codex Agent", "A coding agent")

        await send_bootstrap(adapter, tools=tools)

        assert [e["content"] for e in events_of_type(tools, "thought")] == ["on time"]

    @pytest.mark.asyncio
    async def test_a_process_that_stops_reading_cannot_hold_a_finished_turn(
        self,
    ) -> None:
        client = StopsReadingAfterTurnClient(events=[turn_completed()])
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(turn_settle_timeout_s=0.01)
        )
        await adapter.on_started("Codex Agent", "A coding agent")

        await asyncio.wait_for(send_bootstrap(adapter), timeout=5)

        assert adapter._room_clients[ROOM_ID].turn is None

    @pytest.mark.asyncio
    async def test_a_request_from_a_process_that_exited_is_never_put_to_the_room(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(*parked_on_approval(PROCESS_EXITED))

        with pytest.raises(TurnResultAlreadyReported):
            await asyncio.wait_for(room.send("run tests"), timeout=5)

        assert room.chat == []
        assert [f["message"] for f in reported_failures(room.deliveries[0])] == [
            TRANSPORT_LOST_ERROR
        ]

    @pytest.mark.asyncio
    async def test_a_decision_the_exited_process_cannot_read_still_reports_the_loss(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        exits = ExitsWhileAskedClient(events=list(parked_on_approval()))
        room = await codex_room(client=exits, approval_wait_timeout_s=3600)
        await room.send("run tests")

        exits.emit(PROCESS_EXITED)
        with pytest.raises(TurnResultAlreadyReported):
            await asyncio.wait_for(room.settled(), timeout=5)

        assert [f["message"] for f in reported_failures(room.deliveries[0])] == [
            TRANSPORT_LOST_ERROR
        ]

    @pytest.mark.asyncio
    async def test_a_stale_process_loss_cannot_retire_its_replacement(self) -> None:
        client = FakeCodexClient(
            events=[
                PROCESS_EXITED,
                turn_completed("turn-2"),
            ]
        )
        adapter = make_codex_adapter(client)
        await adapter.on_started("Codex Agent", "A coding agent")
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter)
        room = adapter._room_clients[ROOM_ID]
        lost = room.generation
        await send_bootstrap(adapter)

        adapter._lose_process(
            room, lost, event_notification(TRANSPORT_CLOSED, {"reason": "late"})
        )

        assert room.generation == lost + 1
        assert room.residency.snapshot.state == Residency.RESIDENT

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("restore", "refusal"),
        [
            ({"resumed_settings": {"modelProvider": "other"}}, "model_provider"),
            ({"resumed_thread_id": "thr-elsewhere"}, "thr-elsewhere"),
            (
                {
                    "resume_error": CodexJsonRpcError(
                        code=-32600, message="no rollout found for thread id thr-1"
                    )
                },
                "no rollout found",
            ),
        ],
        ids=["changed-settings", "other-thread", "no-rollout"],
    )
    async def test_a_refused_restore_keeps_the_thread_and_starts_no_other(
        self, restore: dict[str, Any], refusal: str
    ) -> None:
        client = FakeCodexClient(
            events=[PROCESS_EXITED],
            started_settings={"modelProvider": "openai"},
            **restore,
        )
        adapter = make_codex_adapter(client)
        await adapter.on_started("Codex Agent", "A coding agent")
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter)
        tools = ToolSchemaFakeTools()

        with pytest.raises((CodexContinuityError, CodexJsonRpcError), match=refusal):
            await send_bootstrap(adapter, tools=tools)

        assert adapter._room_threads[ROOM_ID] == "thr-1"
        assert client.request_methods.count(CodexRequestMethod.THREAD_START) == 1
        assert refusal in reported_failures(tools)[0]["message"]
