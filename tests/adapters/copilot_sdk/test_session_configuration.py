"""Session creation kwargs: system prompt, model, tools, provider, session id."""

from __future__ import annotations

import pytest

from band.adapters.copilot_sdk import (
    _COPILOT_SDK_AVAILABLE,
    CopilotSDKAdapter,
    CopilotSDKAdapterConfig,
)
from band.core.exceptions import BandConfigError
from band.integrations.copilot_sdk import (
    ASK_USER_ROOM,
    ROOM_ASK_USER_GUIDANCE,
    TURN_COMPLETION_GUIDANCE,
)
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot import ProviderConfig


class TestSessionConfiguration:
    @pytest.mark.asyncio
    async def test_system_message_replaces_with_rendered_prompt(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(custom_section="Always rhyme.")
        )
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        system_message = client.sessions[0].kwargs["system_message"]
        assert system_message["mode"] == "replace"
        assert "Copilot Agent" in system_message["content"]
        assert "Always rhyme." in system_message["content"]
        # Turn-completion guidance is unconditional (no ask_user here): "replace"
        # mode strips the CLI's own task-completion section, so without this the
        # model loops on the runtime's "continue" nudge, never going idle.
        assert TURN_COMPLETION_GUIDANCE in system_message["content"]

    @pytest.mark.asyncio
    async def test_model_and_reasoning_effort_forwarded(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(model="gpt-5", reasoning_effort="high")
        )
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        kwargs = client.sessions[0].kwargs
        assert kwargs["model"] == "gpt-5"
        assert kwargs["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_available_tools_restricted_to_bridged_names(self):
        """Built-in Copilot tools stay off: only bridged tools are available."""
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        kwargs = client.sessions[0].kwargs
        bridged_names = [t.name for t in kwargs["tools"]]
        assert bridged_names == ["band_send_message", "band_get_participants"]
        assert kwargs["available_tools"] == bridged_names
        assert all(t.skip_permission for t in kwargs["tools"])

    @pytest.mark.asyncio
    async def test_ask_user_handler_forwarded_and_tool_enabled(self):
        async def ask_operator(request, context):
            return {"answer": "beta", "wasFreeform": False}

        client = FakeCopilotClient()
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(ask_user=ask_operator)
        )
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        kwargs = client.sessions[0].kwargs
        assert kwargs["on_user_input_request"] is ask_operator
        # ask_user must be allowlisted alongside the bridged Band tools,
        # or the built-in tool stays filtered out and the handler is dead.
        assert "ask_user" in kwargs["available_tools"]
        assert "band_send_message" in kwargs["available_tools"]
        # A caller-supplied handler answers from outside the room; the
        # room-mode turn-split contract must stay out of its prompt.
        assert ROOM_ASK_USER_GUIDANCE not in kwargs["system_message"]["content"]

    @pytest.mark.asyncio
    async def test_ask_user_room_registers_adapter_bridge(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(ask_user=ASK_USER_ROOM)
        )
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        kwargs = client.sessions[0].kwargs
        assert callable(kwargs["on_user_input_request"])
        assert "ask_user" in kwargs["available_tools"]
        # The model learns the turn-split contract from the system prompt,
        # not first-hand from its first tool result.
        assert ROOM_ASK_USER_GUIDANCE in kwargs["system_message"]["content"]

    @pytest.mark.asyncio
    async def test_ask_user_off_by_default(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        kwargs = client.sessions[0].kwargs
        assert "on_user_input_request" not in kwargs
        assert "ask_user" not in kwargs["available_tools"]

    def test_invalid_ask_user_value_rejected(self):
        with pytest.raises(BandConfigError, match="ask_user"):
            CopilotSDKAdapter(
                CopilotSDKAdapterConfig(ask_user="console"),  # type: ignore[arg-type]
                client_factory=FakeCopilotClient,
            )

    @pytest.mark.asyncio
    async def test_byok_provider_forwarded(self):
        provider = ProviderConfig(
            type="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
        )
        client = FakeCopilotClient()
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(model="gpt-4o-mini", provider=provider)
        )
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        kwargs = client.sessions[0].kwargs
        assert kwargs["provider"] == provider
        assert kwargs["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_deterministic_session_id_from_room(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(session_id_prefix="myagent-")
        )
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools, room_id="room-42")

        assert client.sessions[0].session_id == "myagent-room-42"
