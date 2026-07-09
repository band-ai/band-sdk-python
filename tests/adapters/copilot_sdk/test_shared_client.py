"""One CopilotClient shared by several adapters (one client, many sessions)."""

from __future__ import annotations

import pytest

from band.adapters.copilot_sdk import CopilotSDKAdapter, CopilotSDKAdapterConfig
from band.core.exceptions import BandConfigError
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk


class TestSharedClient:
    @pytest.mark.asyncio
    async def test_client_and_client_factory_are_mutually_exclusive(self):
        client = FakeCopilotClient()
        with pytest.raises(BandConfigError):
            CopilotSDKAdapter(client=client, client_factory=lambda: client)

    @pytest.mark.asyncio
    async def test_two_adapters_share_one_client_with_isolated_sessions(self):
        shared = FakeCopilotClient()
        tom = CopilotSDKAdapter(
            CopilotSDKAdapterConfig(session_id_prefix="tom-"), client=shared
        )
        jerry = CopilotSDKAdapter(
            CopilotSDKAdapterConfig(session_id_prefix="jerry-"), client=shared
        )
        await tom.on_started("Tom", "cat")
        await jerry.on_started("Jerry", "mouse")

        # Both agents in the SAME room: distinct prefixes keep sessions apart.
        await run_message(
            tom, ToolSchemaFakeTools(), room_id="room-1", content="for Tom"
        )
        await run_message(
            jerry, ToolSchemaFakeTools(), room_id="room-1", content="for Jerry"
        )

        assert [s.session_id for s in shared.sessions] == ["tom-room-1", "jerry-room-1"]
        assert "for Jerry" not in shared.sessions[0].prompts[0]
        assert "for Tom" not in shared.sessions[1].prompts[0]

    @pytest.mark.asyncio
    async def test_default_session_ids_isolated_per_agent(self):
        """Two default-config agents in the same room never share a session.

        The default prefix folds in the immutable Band agent id, so Tom and
        Jerry on the same host cannot resume each other's persisted state.
        """
        shared = FakeCopilotClient()
        tom = CopilotSDKAdapter(client=shared)
        jerry = CopilotSDKAdapter(client=shared)
        # The Band runtime sets the immutable agent id before on_started.
        tom._band_agent_id = "agent-tom-id"
        jerry._band_agent_id = "agent-jerry-id"
        await tom.on_started("Tom", "cat")
        await jerry.on_started("Jerry", "mouse")

        await run_message(tom, ToolSchemaFakeTools(), room_id="room-1")
        await run_message(jerry, ToolSchemaFakeTools(), room_id="room-1")

        ids = [session.session_id for session in shared.sessions]
        assert ids == ["band-agent-tom-id-room-1", "band-agent-jerry-id-room-1"]

    @pytest.mark.asyncio
    async def test_borrowed_client_never_stopped(self):
        shared = FakeCopilotClient()
        tom = CopilotSDKAdapter(
            CopilotSDKAdapterConfig(session_id_prefix="tom-"), client=shared
        )
        jerry = CopilotSDKAdapter(
            CopilotSDKAdapterConfig(session_id_prefix="jerry-"), client=shared
        )
        await tom.on_started("Tom", "cat")
        await jerry.on_started("Jerry", "mouse")
        await run_message(tom, ToolSchemaFakeTools(), room_id="room-1")
        await run_message(jerry, ToolSchemaFakeTools(), room_id="room-1")

        # Tom leaving (last room) or shutting down must not kill Jerry's client.
        await tom.on_cleanup("room-1")
        await tom.cleanup_all()

        assert shared.sessions[0].disconnected  # Tom's session released
        assert not shared.sessions[1].disconnected  # Jerry's untouched
        assert not shared.stopped

        # Jerry keeps working on the shared client afterwards.
        await run_message(
            jerry, ToolSchemaFakeTools(), room_id="room-1", is_session_bootstrap=False
        )
        assert len(shared.sessions[1].prompts) == 2

    @pytest.mark.asyncio
    async def test_owned_client_still_stopped(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        await run_message(adapter, ToolSchemaFakeTools())

        await adapter.cleanup_all()

        assert client.stopped
