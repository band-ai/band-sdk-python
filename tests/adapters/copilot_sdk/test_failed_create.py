"""A fresh session whose creation fails or is interrupted leaves nothing on disk."""

from __future__ import annotations

import asyncio

import pytest

from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    interrupt_when,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk


class TestFailedCreate:
    async def test_an_interrupted_create_deletes_the_session_it_asked_for(self):
        client = FakeCopilotClient()
        client.hold_create = True
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await interrupt_when(
            asyncio.create_task(run_message(adapter, tools)), client.creating
        )
        await run_message(adapter, tools, is_session_bootstrap=False)

        interrupted, created = client.create_calls
        assert client.deleted == [interrupted]
        assert [session.session_id for session in client.sessions] == [created]

    async def test_a_failed_create_deletes_the_session_it_asked_for(self):
        client = FakeCopilotClient(create_error=RuntimeError("runtime refused"))
        adapter = await make_started_adapter(client)

        with pytest.raises(RuntimeError, match="runtime refused"):
            await run_message(adapter, ToolSchemaFakeTools())

        (asked,) = client.create_calls
        assert client.deleted == [asked]
