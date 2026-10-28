"""Codex adapter: model selection and turn-start errors."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.core.defaultmodels import OPENAI_MODEL
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.types import (
    CodexRequestMethod,
    CodexSessionState,
)
from band.testing import (
    reported_failures,
)
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    make_platform_message,
    start_room_process,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
)
from tests.adapters.dispatch import deliver_message


class TestModelSelection:
    @pytest.mark.asyncio
    async def test_auto_selected_model_error_propagates_without_retry(self) -> None:
        """Auto-selected model errors propagate instead of trying another model."""
        fake_client = FakeCodexClient(
            turn_start_error=CodexJsonRpcError(
                code=-32000,
                message="Model gpt-5.5 is not available for this account",
            ),
            turn_start_error_once=False,
            model_list_result={
                "data": [
                    {"id": "gpt-5.5", "hidden": False},
                    {"id": "gpt-6-luna", "hidden": False},
                ]
            },
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig(model=None))
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id=ROOM_ID, content="hello")
        with pytest.raises(CodexJsonRpcError, match="not available"):
            await deliver_message(
                adapter,
                msg,
                tools,
                CodexSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id=ROOM_ID,
            )

        assert fake_client.request_methods.count(CodexRequestMethod.TURN_START) == 1
        assert adapter._selected_model == "gpt-5.5"

    @pytest.mark.asyncio
    async def test_model_selection_uses_first_visible_model(self) -> None:
        """Auto-selection uses Codex's first visible model without fallback ordering."""
        fake_client = FakeCodexClient(
            model_list_result={
                "data": [
                    {"id": "gpt-5.6-sol", "hidden": False},
                    {"id": OPENAI_MODEL, "hidden": False},
                ]
            },
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig(model=None))
        await adapter.on_started("Agent", "An agent")
        await start_room_process(adapter)

        assert adapter._room_clients[ROOM_ID].selected_model == "gpt-5.6-sol"

    @pytest.mark.asyncio
    async def test_explicit_model_error_propagates_without_fallback(self) -> None:
        """When the user explicitly set a model, errors propagate — no silent fallback."""
        fake_client = FakeCodexClient(
            turn_start_error=CodexJsonRpcError(
                code=-32000,
                message="Model unavailable-test-model is not available",
            ),
            turn_start_error_once=False,
            model_list_result={
                "data": [
                    {"id": "gpt-5.5", "hidden": False},
                    {"id": "gpt-6-luna", "hidden": False},
                ]
            },
        )
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(model="unavailable-test-model")
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id=ROOM_ID, content="hello")
        with pytest.raises(CodexJsonRpcError, match="not available"):
            await deliver_message(
                adapter,
                msg,
                tools,
                CodexSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id=ROOM_ID,
            )

        assert CodexRequestMethod.MODEL_LIST not in fake_client.request_methods

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "codex"
        assert "not available" in failures[0]["message"]

    @pytest.mark.asyncio
    async def test_model_selection_uses_default_when_model_list_empty(self) -> None:
        """Auto-selection uses the adapter default when Codex returns no visible models."""
        fake_client = FakeCodexClient(model_list_result={"data": []})
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig(model=None))
        await adapter.on_started("Agent", "An agent")
        await start_room_process(adapter)

        assert adapter._room_clients[ROOM_ID].selected_model == OPENAI_MODEL

    @pytest.mark.asyncio
    async def test_model_selection_uses_default_when_model_list_fails(self) -> None:
        """Auto-selection uses the adapter default if model discovery fails."""
        fake_client = FakeCodexClient(
            model_list_error=RuntimeError("model/list unavailable")
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig(model=None))
        await adapter.on_started("Agent", "An agent")
        await start_room_process(adapter)

        assert adapter._room_clients[ROOM_ID].selected_model == OPENAI_MODEL

    @pytest.mark.asyncio
    async def test_non_model_error_propagates(self) -> None:
        """Non-model-related errors propagate from turn startup."""
        fake_client = FakeCodexClient(
            turn_start_error=CodexJsonRpcError(
                code=-32001, message="Server overloaded"
            ),
            turn_start_error_once=False,
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig(model=None))
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id=ROOM_ID, content="hello")
        with pytest.raises(CodexJsonRpcError, match="overloaded"):
            await deliver_message(
                adapter,
                msg,
                tools,
                CodexSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id=ROOM_ID,
            )
