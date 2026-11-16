"""Abandoned session setup stays owned until its late result is retired."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.copilot_sdk import CopilotSDKAdapterConfig
from band.converters.copilot_sdk import CopilotSDKSessionState
from band.core.exceptions import ProviderUnavailableError
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)
from tests.runtime.conftest import wait_for_condition

pytestmark = [requires_copilot_sdk, pytest.mark.looptime]


class TestSetupRecovery:
    async def test_resume_deadline_never_creates_a_replacement_beside_late_setup(
        self,
    ) -> None:
        client = FakeCopilotClient()
        client.hold_resume = True
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(transition_timeout_s=1)
        )
        tools = ToolSchemaFakeTools()
        history = CopilotSDKSessionState(room_to_session={"room-1": "recorded"})
        turn = asyncio.create_task(run_message(adapter, tools, history=history))
        try:
            await client.resuming.wait()
            with pytest.raises(TimeoutError):
                await turn
            await adapter.backend_turns.settled("room-1")
            with pytest.raises(ProviderUnavailableError):
                await run_message(adapter, tools, history=history)
            assert client.create_calls == []
            assert client.sessions == []
        finally:
            client.resume_released.set()
            await wait_for_condition(lambda: bool(client.sessions))
            await wait_for_condition(
                lambda: not adapter.backend_turns.unavailable("room-1")
            )
        client.hold_resume = False
        await run_message(adapter, tools, history=history)
        assert len(client.sessions) == 1
        assert len(client.sessions[0].prompts) == 1
        await adapter.cleanup_all()

    async def test_creation_cleanup_cannot_extend_the_callers_setup_budget(
        self,
    ) -> None:
        client = FakeCopilotClient(create_error=RuntimeError("creation rejected"))
        client.hold_delete = True
        client.resist_delete_cancel = True
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(transition_timeout_s=1)
        )
        tools = ToolSchemaFakeTools()
        turn = asyncio.create_task(run_message(adapter, tools))
        try:
            await client.deleting.wait()
            with pytest.raises(TimeoutError):
                await turn
            await adapter.backend_turns.settled("room-1")
            with pytest.raises(ProviderUnavailableError):
                await run_message(adapter, tools)
            assert len(client.create_calls) == 1
            assert client.deleted == []
        finally:
            client.delete_released.set()
            await wait_for_condition(lambda: client.deleted == client.create_calls)
            await wait_for_condition(
                lambda: not adapter.backend_turns.stopping("room-1")
            )
        client.create_error = None
        client.hold_delete = False
        await run_message(adapter, tools)
        assert len(client.create_calls) == 2
        assert len(client.sessions[0].prompts) == 1
        await adapter.cleanup_all()
