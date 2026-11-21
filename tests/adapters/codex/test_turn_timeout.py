"""Codex adapter: a turn past its deadline is interrupted and reported."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import httpx
import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.protocols import (
    FAILURE_CODE_TIMEOUT,
    FailureMetadataKey,
    TurnResultAlreadyReported,
)
from band.core.types import MessageType
from band.integrations.codex.types import CodexNotificationMethod, CodexRequestMethod
from band.runtime.tools.agent import AgentTools
from band.testing import (
    reported_failures,
)
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    agent_input,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
)
from tests.runtime.helpers import rest_client_over

REPORTED_ERROR = "Something went wrong"


class TestTurnTimeout:
    @pytest.mark.asyncio
    async def test_turn_timeout_sends_interrupt_and_clean_error(self) -> None:
        """When recv_event times out, the adapter sends turn/interrupt, reports
        the failure, and fails the turn so its delivery is marked FAILED --
        same as every sibling adapter's own turn-timeout handling."""
        # No events means FakeCodexClient raises asyncio.TimeoutError immediately.
        fake_client = FakeCodexClient(events=[])
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(turn_timeout_s=0.01)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter, tools=tools)

        # Adapter should have sent turn/interrupt with both identifiers.
        assert fake_client.params_of(CodexRequestMethod.TURN_INTERRUPT) == [
            {"threadId": "thr-1", "turnId": "turn-1"}
        ]

        # The turn fails the platform's turn, so no separate "I stopped..."
        # chat reply goes out alongside the structured failure event.
        assert not tools.messages_sent

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "codex"
        assert failures[0]["code"] == "timeout"

    @pytest.mark.asyncio
    async def test_turn_timeout_emits_failed_lifecycle_event(self) -> None:
        """A timed-out turn still gets a failed-status lifecycle event, same
        as the transport/closed and turn/completed(status=failed) paths --
        the timeout branch's TurnResultAlreadyReported raise happens from
        inside its own except clause, so it can't rely on the sibling
        `except TurnResultAlreadyReported` handler to emit it."""
        fake_client = FakeCodexClient(events=[])
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                turn_timeout_s=0.01,
                emit_turn_lifecycle_events=True,
            ),
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter, tools=tools)

        lifecycle_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_lifecycle"
        ]
        assert len(lifecycle_events) == 2
        assert lifecycle_events[0]["metadata"]["codex_turn_status"] == "started"
        assert lifecycle_events[1]["metadata"]["codex_turn_status"] == "failed"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "interrupt_error",
        [None, ConnectionResetError("Codex process is gone")],
        ids=["interrupted", "interrupt-failed"],
    )
    async def test_a_timed_out_turn_is_reported_once_and_the_room_serves_on(
        self, interrupt_error: Exception | None
    ) -> None:
        """Whether or not Codex takes the interrupt, the timeout is the turn's
        one failure and the room's next message still reaches Codex."""
        client = FakeCodexClient(events=[], interrupt_error=interrupt_error)
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(turn_timeout_s=0.01)
        )
        deliveries = [ToolSchemaFakeTools(), ToolSchemaFakeTools()]
        await adapter.on_started("Codex Agent", "A coding agent")

        for tools in deliveries:
            with pytest.raises(TurnResultAlreadyReported):
                await send_bootstrap(adapter, tools=tools)

        assert [
            [failure["code"] for failure in reported_failures(tools)]
            for tools in deliveries
        ] == [[FAILURE_CODE_TIMEOUT], [FAILURE_CODE_TIMEOUT]]
        assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2

    @pytest.mark.looptime
    @pytest.mark.parametrize(
        ("idle_timeout", "timeout_message"),
        [
            pytest.param(None, "Codex turn timed out after 10.0s", id="total"),
            pytest.param(
                2.0,
                "Codex turn timed out after 2.0s of silence (turn_idle_timeout_s)",
                id="idle",
            ),
        ],
    )
    async def test_a_turn_that_reported_an_error_times_out_without_a_second_failure(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        idle_timeout: float | None,
        timeout_message: str,
    ) -> None:
        """Codex reports an error and never completes the turn. The limit still
        interrupts it, and the error stays the turn's one failure."""
        client = FakeCodexClient(
            events=[
                event_notification(
                    CodexNotificationMethod.ERROR,
                    {"error": {"message": REPORTED_ERROR}, "willRetry": False},
                )
            ]
        )
        adapter = await started_codex(
            client, turn_timeout_s=10.0, turn_idle_timeout_s=idle_timeout
        )
        tools = ToolSchemaFakeTools()

        with pytest.raises(TurnResultAlreadyReported) as timeout:
            await send_bootstrap(adapter, tools=tools)

        assert str(timeout.value) == timeout_message
        assert [failure["message"] for failure in reported_failures(tools)] == [
            REPORTED_ERROR
        ]
        assert client.params_of(CodexRequestMethod.TURN_INTERRUPT) == [
            {"threadId": FIRST_THREAD_ID, "turnId": "turn-1"}
        ]

    @pytest.mark.looptime
    async def test_timeout_reports_when_the_earlier_error_could_not_be_posted(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        written_codes: list[str] = []

        def post_event(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST" and request.url.path.endswith("/events")
            event = json.loads(request.content)["event"]
            assert event["message_type"] == MessageType.ERROR
            if event["content"] == REPORTED_ERROR:
                raise httpx.ConnectError(
                    "first failure delivery unavailable", request=request
                )
            written_codes.append(event["metadata"][FailureMetadataKey.FAILURE]["code"])
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "posted",
                        "message_type": MessageType.ERROR,
                        "success": True,
                    }
                },
            )

        client = FakeCodexClient(
            events=[
                event_notification(
                    CodexNotificationMethod.ERROR,
                    {"error": {"message": REPORTED_ERROR}, "willRetry": False},
                )
            ]
        )
        adapter = await started_codex(client, features={"emit": ()})
        async with rest_client_over(post_event) as rest:
            tools = AgentTools(ROOM_ID, rest)
            with pytest.raises(TurnResultAlreadyReported):
                await adapter.on_event(agent_input("start", tools))

        assert written_codes == [FAILURE_CODE_TIMEOUT]
