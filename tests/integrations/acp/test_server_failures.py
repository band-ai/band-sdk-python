"""ACP server prompt outcomes at the Band room boundary."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.exceptions import RequestError
from band_sdk_core import AgentFailure

from band.agent import Agent
from band.core.types import MessageType
from band.integrations.acp.server import ACPServer
from band.integrations.acp.server_adapter import BandACPServerAdapter
from tests.integrations.acp.conftest import (
    deliver_server_message,
    failure_event,
    make_platform_message,
    make_tool_call_message,
    wait_for_pending_prompt,
)


def running_server(rest: MagicMock) -> tuple[BandACPServerAdapter, ACPServer]:
    adapter = BandACPServerAdapter(rest_client=rest)
    adapter._session_to_room["session-1"] = "room-123"
    adapter._room_to_session["room-123"] = "session-1"
    adapter.set_acp_client(AsyncMock())
    return adapter, ACPServer(adapter)


def prompt(server: ACPServer) -> asyncio.Task[object]:
    return asyncio.create_task(
        server.prompt(session_id="session-1", prompt=[{"text": "Hello"}])
    )


def assert_default_room_failure(error: RequestError) -> None:
    assert error.code == -32603
    assert error.data == AgentFailure("band", "Peer failed").to_extension_data()


@pytest.mark.asyncio
async def test_error_rejects_with_redacted_core_projection(
    mock_rest_client: MagicMock,
) -> None:
    adapter, server = running_server(mock_rest_client)
    task = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    msg = failure_event(
        {
            "failure": {
                "provider": "peer",
                "message": "Authorization: ApiKey sk-private",
                "code": "UPSTREAM",
                "detail": {"nested": ["Bearer secret-token"]},
            }
        },
        content="token=room-secret",
    )

    await deliver_server_message(adapter, msg)

    expected = AgentFailure(
        "peer",
        "Authorization=[REDACTED]",
        code="UPSTREAM",
        detail={"nested": ["Bearer [REDACTED]"]},
    ).to_extension_data()
    with pytest.raises(RequestError) as raised:
        await task
    assert raised.value.code == -32603
    assert raised.value.data == expected
    update = adapter.get_acp_client().session_update.await_args.kwargs["update"]
    assert update.field_meta == expected
    assert update.content.text == "[Error] token=[REDACTED]"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {},
        [],
        {"failure": "broken"},
        {"failure": {"provider": " ", "message": "x"}},
        {"failure": {"provider": "peer", "message": "x", "code": 42}},
        {"failure": {"provider": "peer", "message": "x", "detail": {"bad": object()}}},
    ],
)
async def test_malformed_failure_uses_safe_fallback(
    mock_rest_client: MagicMock, metadata: object
) -> None:
    adapter, server = running_server(mock_rest_client)
    task = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    await deliver_server_message(adapter, failure_event(metadata, "Bearer secret"))
    with pytest.raises(RequestError) as raised:
        await task
    assert (
        raised.value.data
        == AgentFailure("band", "Bearer [REDACTED]").to_extension_data()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["", "   "])
async def test_blank_failure_message_keeps_core_failure(
    mock_rest_client: MagicMock, message: str
) -> None:
    adapter, server = running_server(mock_rest_client)
    task = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    await deliver_server_message(
        adapter,
        failure_event(
            {
                "failure": {
                    "provider": "codex",
                    "message": message,
                    "code": "timeout",
                    "detail": {"retry": 1},
                }
            },
            content="codex failed without an error message.",
        ),
    )
    with pytest.raises(RequestError) as raised:
        await task
    assert (
        raised.value.data
        == AgentFailure(
            "codex",
            "codex failed without an error message.",
            code="timeout",
            detail={"retry": 1},
        ).to_extension_data()
    )


@pytest.mark.asyncio
async def test_blank_error_uses_generic_fallback(mock_rest_client: MagicMock) -> None:
    adapter, server = running_server(mock_rest_client)
    task = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    await deliver_server_message(adapter, failure_event(content=" "))
    with pytest.raises(RequestError) as raised:
        await task
    assert (
        raised.value.data
        == AgentFailure("band", "Band peer reported a failure.").to_extension_data()
    )


@pytest.mark.asyncio
async def test_server_rejects_before_slow_update_and_later_cancel(
    mock_rest_client: MagicMock,
) -> None:
    adapter, server = running_server(mock_rest_client)
    release = asyncio.Event()
    entered = asyncio.Event()

    async def slow_update(**kwargs: object) -> None:
        entered.set()
        await release.wait()

    adapter.get_acp_client().session_update = AsyncMock(side_effect=slow_update)
    task = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    delivery = asyncio.create_task(deliver_server_message(adapter, failure_event()))
    await entered.wait()
    try:
        await adapter.cancel_prompt("session-1")
        with pytest.raises(RequestError) as raised:
            await asyncio.wait_for(task, 1)
        assert_default_room_failure(raised.value)
    finally:
        release.set()
        await delivery


@pytest.mark.asyncio
async def test_room_event_before_post_does_not_drop_the_prompt(
    mock_rest_client: MagicMock,
) -> None:
    adapter, server = running_server(mock_rest_client)
    release = asyncio.Event()
    entered = asyncio.Event()
    participants = mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value
    push = MagicMock()
    push.handle_push_event = AsyncMock()
    adapter.set_push_handler(push)

    async def delayed_participants(**kwargs: object) -> object:
        entered.set()
        await release.wait()
        return participants

    mock_rest_client.agent_api_participants.list_agent_chat_participants = AsyncMock(
        side_effect=delayed_participants
    )
    task = prompt(server)
    await entered.wait()
    await deliver_server_message(adapter, failure_event())
    assert not task.done()
    push.handle_push_event.assert_awaited_once()
    release.set()
    await wait_for_pending_prompt(adapter, "room-123")
    mock_rest_client.agent_api_messages.create_agent_chat_message.assert_awaited()
    await deliver_server_message(
        adapter, make_platform_message("Done", message_type=MessageType.TEXT)
    )
    assert (await task).stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_lookup_failure_is_not_hidden_by_an_earlier_room_event(
    mock_rest_client: MagicMock,
) -> None:
    adapter, server = running_server(mock_rest_client)
    release = asyncio.Event()
    entered = asyncio.Event()

    async def failed_participants(**kwargs: object) -> None:
        entered.set()
        await release.wait()
        raise RuntimeError("REST failed")

    mock_rest_client.agent_api_participants.list_agent_chat_participants = AsyncMock(
        side_effect=failed_participants
    )
    task = prompt(server)
    await entered.wait()
    await deliver_server_message(adapter, failure_event())
    assert not task.done()
    release.set()
    with pytest.raises(RuntimeError, match="REST failed"):
        await task
    mock_rest_client.agent_api_messages.create_agent_chat_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_error_update_does_not_change_prompt_failure(
    mock_rest_client: MagicMock,
) -> None:
    adapter, server = running_server(mock_rest_client)
    adapter.get_acp_client().session_update = AsyncMock(
        side_effect=RuntimeError("Editor unavailable")
    )
    task = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    await deliver_server_message(adapter, failure_event())
    with pytest.raises(RequestError) as raised:
        await task
    assert_default_room_failure(raised.value)


@pytest.mark.asyncio
async def test_tool_activity_after_text_keeps_the_prompt_open(
    mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "band.integrations.acp.server_adapter._PROMPT_COMPLETION_GRACE_SECONDS", 0.05
    )
    adapter, server = running_server(mock_rest_client)
    task = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    await deliver_server_message(
        adapter, make_platform_message("Looking into it", message_type=MessageType.TEXT)
    )
    await asyncio.sleep(0.03)
    await deliver_server_message(adapter, make_tool_call_message())
    await asyncio.sleep(0.03)
    assert not task.done()
    await asyncio.sleep(0.08)
    assert (await task).stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_text_grace_yields_to_error(
    mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "band.integrations.acp.server_adapter._PROMPT_COMPLETION_GRACE_SECONDS", 0.05
    )
    adapter, server = running_server(mock_rest_client)
    task = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    await deliver_server_message(
        adapter, make_platform_message("Part", message_type=MessageType.TEXT)
    )
    await deliver_server_message(
        adapter, make_platform_message("Thinking", message_type=MessageType.THOUGHT)
    )
    await deliver_server_message(adapter, failure_event())
    with pytest.raises(RequestError) as raised:
        await task
    assert_default_room_failure(raised.value)


@pytest.mark.asyncio
async def test_old_text_timer_cannot_complete_next_prompt(
    mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "band.integrations.acp.server_adapter._PROMPT_COMPLETION_GRACE_SECONDS", 0.01
    )
    adapter, server = running_server(mock_rest_client)
    first = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    await deliver_server_message(
        adapter, make_platform_message("Part", message_type=MessageType.TEXT)
    )
    await deliver_server_message(adapter, failure_event())
    with pytest.raises(RequestError) as raised:
        await first
    assert_default_room_failure(raised.value)

    second = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    await asyncio.sleep(0.03)
    assert not second.done()
    await adapter.cancel_prompt("session-1")
    assert (await second).stop_reason == "cancelled"


@pytest.mark.asyncio
async def test_room_cleanup_fails_pending_prompt(mock_rest_client: MagicMock) -> None:
    adapter, server = running_server(mock_rest_client)
    task = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    await adapter.on_cleanup("room-123")
    with pytest.raises(RequestError) as raised:
        await task
    assert raised.value.code == -32603
    assert (
        raised.value.data
        == AgentFailure(
            "band", "Band room closed before prompt completed."
        ).to_extension_data()
    )


@pytest.mark.asyncio
async def test_agent_stop_fails_pending_prompt(mock_rest_client: MagicMock) -> None:
    adapter, server = running_server(mock_rest_client)
    runtime = MagicMock()
    runtime.agent_name = "TestBot"
    runtime.agent_description = "ACP test agent"
    runtime.feature_flags = None
    runtime.initialize = AsyncMock()
    runtime.start = AsyncMock()
    runtime.stop = AsyncMock(return_value=True)
    agent = Agent(runtime=runtime, adapter=adapter)
    await agent.start()

    task = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    await agent.stop()
    with pytest.raises(RequestError) as raised:
        await task
    assert raised.value.code == -32603
    assert (
        raised.value.data
        == AgentFailure(
            "band", "Band agent stopped before prompt completed."
        ).to_extension_data()
    )


@pytest.mark.asyncio
async def test_cancel_before_error_keeps_cancelled_outcome(
    mock_rest_client: MagicMock,
) -> None:
    adapter, server = running_server(mock_rest_client)
    task = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    await adapter.cancel_prompt("session-1")
    await deliver_server_message(adapter, failure_event())
    assert (await task).stop_reason == "cancelled"


@pytest.mark.asyncio
async def test_cancel_during_participant_lookup_does_not_post_prompt(
    mock_rest_client: MagicMock,
) -> None:
    adapter, server = running_server(mock_rest_client)
    entered = asyncio.Event()
    release = asyncio.Event()
    participants = mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value

    async def delayed_participants(**kwargs: object) -> object:
        entered.set()
        await release.wait()
        return participants

    mock_rest_client.agent_api_participants.list_agent_chat_participants = AsyncMock(
        side_effect=delayed_participants
    )
    task = prompt(server)
    await entered.wait()
    await adapter.cancel_prompt("session-1")
    try:
        assert (
            await asyncio.wait_for(asyncio.shield(task), 1)
        ).stop_reason == "cancelled"
    finally:
        release.set()
        if not task.done():
            await task
    mock_rest_client.agent_api_messages.create_agent_chat_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_same_room_prompt_does_not_replace_first(
    mock_rest_client: MagicMock,
) -> None:
    adapter, server = running_server(mock_rest_client)
    first = prompt(server)
    await wait_for_pending_prompt(adapter, "room-123")
    with pytest.raises(RequestError) as raised:
        await server.prompt(session_id="session-1", prompt=[{"text": "Second"}])
    assert raised.value.code == -32602
    await deliver_server_message(
        adapter, make_platform_message("Done", message_type=MessageType.TEXT)
    )
    assert (await first).stop_reason == "end_turn"
    mock_rest_client.agent_api_messages.create_agent_chat_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_other_room_remains_independent(mock_rest_client: MagicMock) -> None:
    adapter, server = running_server(mock_rest_client)
    adapter._session_to_room["session-2"] = "room-2"
    adapter._room_to_session["room-2"] = "session-2"
    first = prompt(server)
    second = asyncio.create_task(
        server.prompt(session_id="session-2", prompt=[{"text": "Other"}])
    )
    await wait_for_pending_prompt(adapter, "room-123")
    await wait_for_pending_prompt(adapter, "room-2")
    await deliver_server_message(adapter, failure_event())
    with pytest.raises(RequestError) as raised:
        await first
    assert_default_room_failure(raised.value)
    assert not second.done()
    await deliver_server_message(
        adapter, make_platform_message("Other done", room_id="room-2")
    )
    assert (await second).stop_reason == "end_turn"
