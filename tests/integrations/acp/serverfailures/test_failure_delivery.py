from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.exceptions import RequestError

from tests.integrations.acp.conftest import (
    deliver_server_message,
    failure_event,
    make_platform_message,
    wait_for_pending_prompt,
)
from tests.integrations.acp.serverfailures.kit import (
    assert_default_room_failure,
    prompt,
    running_server,
)


class TestFailureDelivery:
    @pytest.mark.asyncio
    async def test_server_rejects_before_slow_update_and_later_cancel(
        self,
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
    async def test_failed_error_update_does_not_change_prompt_failure(
        self,
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
    async def test_failed_text_update_still_completes_the_prompt(
        self,
        mock_rest_client: MagicMock,
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        adapter.get_acp_client().session_update = AsyncMock(
            side_effect=RuntimeError("Editor unavailable")
        )
        task = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        await deliver_server_message(adapter, make_platform_message("Done"))
        assert (await asyncio.wait_for(task, 5)).stop_reason == "end_turn"
