"""Codex adapter: /model and /reasoning choose what the next turn runs with."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.integrations.codex.types import (
    CodexRequestMethod,
)
from tests.adapters.codex.kit import (
    LIVE_EFFORTS_MODEL_LIST,
    ROOM_ID,
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
    turn_completed,
)


class TestModelAndReasoningCommands:
    @pytest.mark.asyncio
    async def test_model_command_sets_override_without_starting_turn(self) -> None:
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, content="/model gpt-5.5-codex", tools=tools)

        methods = fake_client.request_methods
        assert CodexRequestMethod.TURN_START not in methods
        assert CodexRequestMethod.THREAD_START not in methods
        assert adapter._selected_model == "gpt-5.5-codex"
        assert len(tools.messages_sent) == 1
        assert (
            "Model override set to `gpt-5.5-codex`" in tools.messages_sent[0]["content"]
        )

    @pytest.mark.asyncio
    async def test_models_alias_lists_models_without_starting_turn(self) -> None:
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, content="/models list", tools=tools)

        methods = fake_client.request_methods
        assert CodexRequestMethod.TURN_START not in methods
        assert CodexRequestMethod.THREAD_START not in methods
        assert methods.count(CodexRequestMethod.MODEL_LIST) >= 1
        assert len(tools.messages_sent) == 1
        assert "Available models" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("effort", ["high", "max"])
    async def test_reasoning_effort_passed_in_turn_overrides(self, effort: str) -> None:
        """Efforts newer than any list the SDK could hard-code reach Codex as-is."""
        fake_client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                reasoning_effort=effort, reasoning_summary="concise"
            ),
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="hello", tools=tools)
        turn_params = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        assert turn_params["effort"] == effort
        assert turn_params["summary"] == "concise"

    @pytest.mark.asyncio
    async def test_reasoning_effort_omitted_when_none(self) -> None:
        fake_client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="hello", tools=tools)
        turn_params = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        assert "effort" not in turn_params
        assert "summary" not in turn_params

    @pytest.mark.asyncio
    @pytest.mark.parametrize("effort", ["high", "ultra"])
    async def test_reasoning_command_sets_effort(self, effort: str) -> None:
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content=f"/reasoning {effort}", tools=tools)
        room = adapter._room_clients[ROOM_ID]
        assert room.reasoning_effort == effort
        assert len(tools.messages_sent) == 1
        assert (
            f"Reasoning effort set to `{effort}`" in tools.messages_sent[0]["content"]
        )

    @pytest.mark.asyncio
    async def test_reasoning_command_lists_efforts_the_model_supports(self) -> None:
        fake_client = FakeCodexClient(model_list_result=LIVE_EFFORTS_MODEL_LIST)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(model="gpt-6-sol")
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="/reasoning", tools=tools)
        assert len(tools.messages_sent) == 1
        assert (
            "`gpt-6-sol` supports: low, max, ultra."
            in tools.messages_sent[0]["content"]
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("effort", "stored", "reply"),
        [
            ("ultra", "ultra", "Reasoning effort set to `ultra`"),
            (
                "hgih",
                None,
                (
                    "`gpt-6-sol` doesn't support reasoning effort `hgih`. "
                    "Supported: low, max, ultra."
                ),
            ),
        ],
    )
    async def test_reasoning_command_checks_the_models_live_efforts(
        self, effort: str, stored: str | None, reply: str
    ) -> None:
        """A typo is refused up front instead of failing every later turn."""
        fake_client = FakeCodexClient(model_list_result=LIVE_EFFORTS_MODEL_LIST)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(model="gpt-6-sol")
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content=f"/reasoning {effort}", tools=tools)
        assert adapter._room_clients[ROOM_ID].reasoning_effort == stored
        assert len(tools.messages_sent) == 1
        assert reply in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_reasoning_command_still_answers_when_model_list_fails(self) -> None:
        fake_client = FakeCodexClient(
            model_list_error=RuntimeError("model/list unavailable")
        )
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(model="gpt-6-sol", reasoning_effort="high"),
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="/reasoning", tools=tools)
        assert len(tools.messages_sent) == 1
        reply = tools.messages_sent[0]["content"]
        assert "Current reasoning effort: `high`" in reply
        assert "supports:" not in reply
