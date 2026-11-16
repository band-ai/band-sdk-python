"""A fresh session whose creation fails or is interrupted leaves nothing on disk."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError, ProviderUnavailableError
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    interrupt_when,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)
from tests.runtime.conftest import wait_for_condition

pytestmark = requires_copilot_sdk


class TestFailedCreate:
    async def test_failed_creation_and_failed_deletion_keep_the_room_unavailable(
        self,
    ) -> None:
        client = FakeCopilotClient(create_error=RuntimeError("creation rejected"))
        client.delete_error = RuntimeError("deletion unconfirmed")
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        with pytest.raises(RuntimeError, match="deletion unconfirmed"):
            await run_message(adapter, tools)
        with pytest.raises(ProviderUnavailableError):
            await run_message(adapter, tools)
        with pytest.raises(CleanupIncompleteError):
            await adapter.cleanup_all()
        # CleanupIncomplete abandons the wait only; no create/delete retry.
        with pytest.raises(ProviderUnavailableError):
            await run_message(adapter, tools)
        assert len(client.create_calls) == 1
        assert client.deleted == []
        assert client.sessions == []

    @pytest.mark.looptime
    async def test_an_interrupted_create_deletes_the_session_it_asked_for(self):
        client = FakeCopilotClient()
        client.hold_create = True
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await interrupt_when(
            asyncio.create_task(run_message(adapter, tools)), client.creating
        )
        with pytest.raises(CleanupIncompleteError):
            await adapter.on_cleanup("room-1")
        client.create_released.set()
        await wait_for_condition(lambda: client.deleted == client.create_calls)
        await wait_for_condition(
            lambda: not adapter.backend_turns.unavailable("room-1")
        )
        await run_message(adapter, tools, is_session_bootstrap=False)

        interrupted, created = client.create_calls
        assert client.deleted == [interrupted]
        assert [session.session_id for session in client.sessions] == [
            interrupted,
            created,
        ]
        assert client.sessions[0].prompts == []

    async def test_a_failed_create_deletes_the_session_it_asked_for(self):
        client = FakeCopilotClient(create_error=RuntimeError("runtime refused"))
        adapter = await make_started_adapter(client)

        with pytest.raises(RuntimeError, match="runtime refused"):
            await run_message(adapter, ToolSchemaFakeTools())

        (asked,) = client.create_calls
        assert client.deleted == [asked]
