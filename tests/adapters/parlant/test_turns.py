"""ParlantAdapter.on_message: one Band turn through the room's Parlant session."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from band.adapters.parlant import ParlantAdapter, ParlantAdapterConfig
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.testing import reported_failures
from tests.adapters.parlant.helpers import agent_event

pytestmark = pytest.mark.usefixtures("parlant_sessions")


@pytest.fixture
def run_turn(sample_message, mock_tools):
    """Run one turn in *room_id* on *adapter*."""

    async def run(adapter: ParlantAdapter, *, room_id: str = "room-123") -> None:
        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=room_id,
        )

    return run


async def test_creates_session_for_room(
    start_adapter, run_turn, mock_parlant_server, mock_app
):
    adapter = await start_adapter()

    await run_turn(adapter)

    mock_parlant_server.create_customer.assert_awaited_once()
    mock_app.sessions.create.assert_awaited_once()
    mock_app.sessions.create_customer_message.assert_awaited_once()


async def test_reuses_existing_session(
    start_adapter, run_turn, mock_parlant_server, mock_app
):
    adapter = await start_adapter()

    await run_turn(adapter)
    await run_turn(adapter)

    mock_parlant_server.create_customer.assert_awaited_once()
    mock_app.sessions.create.assert_awaited_once()


async def test_on_cleanup_forgets_the_rooms_session(
    start_adapter, run_turn, mock_parlant_server, mock_app
):
    adapter = await start_adapter()
    await run_turn(adapter)

    await adapter.on_cleanup("room-123")
    await run_turn(adapter)

    assert mock_parlant_server.create_customer.await_count == 2
    assert mock_app.sessions.create.await_count == 2


async def test_cleanup_all_forgets_every_rooms_session(
    start_adapter, run_turn, mock_app
):
    adapter = await start_adapter()
    await run_turn(adapter, room_id="room-1")
    await run_turn(adapter, room_id="room-2")

    await adapter.cleanup_all()
    await adapter.on_started(agent_name="TestBot", agent_description="A test bot")
    await run_turn(adapter, room_id="room-1")

    assert mock_app.sessions.create.await_count == 3


async def test_binds_session_tools_for_the_turn_only(
    start_adapter, run_turn, mock_tools
):
    """Parlant runs tools on its own tasks, so they find the room by session."""
    adapter = await start_adapter()

    with patch(
        "band.integrations.parlant.sessiontools.set_session_tools"
    ) as set_session_tools:
        await run_turn(adapter)

    assert [c.args for c in set_session_tools.call_args_list] == [
        ("session-123", mock_tools),
        ("session-123", None),
    ]


async def test_unbinds_session_tools_on_error(start_adapter, run_turn, mock_app):
    mock_app.sessions.create_customer_message = AsyncMock(
        side_effect=Exception("API error")
    )
    adapter = await start_adapter()

    with (
        patch(
            "band.integrations.parlant.sessiontools.set_session_tools"
        ) as set_session_tools,
        pytest.raises(Exception, match="API error"),
    ):
        await run_turn(adapter)

    set_session_tools.assert_called_with("session-123", None)


async def test_reports_error_on_failure(start_adapter, run_turn, mock_app, mock_tools):
    mock_app.sessions.create_customer_message = AsyncMock(
        side_effect=Exception("API error")
    )
    adapter = await start_adapter()

    with pytest.raises(Exception, match="API error"):
        await run_turn(adapter)

    [failure] = reported_failures(mock_tools)
    assert failure["provider"] == "parlant"
    assert failure["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE


async def test_reports_error_on_session_init_failure(
    start_adapter, run_turn, mock_app, mock_tools
):
    """A session-creation failure is reported, then fails the turn."""
    mock_app.sessions.create = AsyncMock(side_effect=Exception("db unreachable"))
    adapter = await start_adapter()

    with pytest.raises(Exception, match="db unreachable"):
        await run_turn(adapter)

    [failure] = reported_failures(mock_tools)
    assert failure["provider"] == "parlant"
    assert failure["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE


async def test_send_message_failure_is_not_reported_as_provider_failure(
    start_adapter, run_turn, mock_app, mock_tools
):
    """A Band-side send_message failure while delivering the reply must
    propagate as itself, not get misreported as a Parlant provider failure.

    ``deliver_reply`` wraps the ``send_message`` error in
    ``DeliveryFailedError``; ``on_message``'s dedicated except branch must
    re-raise the original cause before its generic ``except Exception``
    (which reports ``send_failure``) ever sees it.
    """
    mock_app.sessions.wait_for_more_events = AsyncMock(return_value=True)
    mock_app.sessions.find_events = AsyncMock(
        return_value=[agent_event("Hello there!", offset=2)]
    )
    mock_tools.send_message_error = ConnectionError("band down")
    adapter = await start_adapter(
        ParlantAdapterConfig(response_timeout=0.2, response_poll=0.01)
    )

    with pytest.raises(ConnectionError, match="band down"):
        await run_turn(adapter)

    assert not reported_failures(mock_tools)


async def test_handles_uninitialized_app(
    mock_parlant_server, mock_parlant_agent, run_turn, mock_tools
):
    """An unstarted adapter reports the failure, then fails the turn."""
    adapter = ParlantAdapter(
        server=mock_parlant_server, parlant_agent=mock_parlant_agent
    )

    with pytest.raises(RuntimeError, match="not initialized"):
        await run_turn(adapter)

    mock_tools.assert_no_messages_sent()
    [failure] = reported_failures(mock_tools)
    assert failure["provider"] == "parlant"
    assert "not initialized" in failure["message"]
