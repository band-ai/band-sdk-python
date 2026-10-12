"""Codex adapter: every Codex thread receives the mandatory Band transport contract."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex.types import (
    CodexRequestMethod,
    CodexSessionState,
)
from band.runtime.prompts import COMMUNICATION_INSTRUCTIONS
from band.runtime.tools import BandTool
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    make_platform_message,
    run_codex_turn,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.adapters.dispatch import deliver_message


class TestTransportContractPrompt:
    """Every Codex thread receives the mandatory Band transport contract."""

    @pytest.mark.parametrize(
        "config",
        [
            CodexAdapterConfig(),
            CodexAdapterConfig(system_prompt="custom"),
            CodexAdapterConfig(include_base_instructions=False),
        ],
    )
    async def test_codex_transport_contract_is_in_default_and_custom_prompts(
        self,
        config: CodexAdapterConfig,
    ) -> None:
        turn = await run_codex_turn(
            events=[tool_call_request(1, BandTool.NO_REPLY), turn_completed()],
            config=config,
        )
        inputs = turn.client.params_of(CodexRequestMethod.TURN_START)[0]["input"]
        assert inputs[0]["text"].count(COMMUNICATION_INSTRUCTIONS) == 1

    async def test_restored_codex_thread_receives_the_mandatory_transport_contract(
        self,
    ) -> None:
        client = FakeCodexClient(
            events=[tool_call_request(1, BandTool.NO_REPLY), turn_completed()]
        )
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(system_prompt="custom")
        )
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            ToolSchemaFakeTools(),
            CodexSessionState(thread_id="restored"),
            None,
            None,
            is_session_bootstrap=True,
            room_id=ROOM_ID,
        )
        inputs = client.params_of(CodexRequestMethod.TURN_START)[0]["input"]
        assert inputs[0]["text"].count(COMMUNICATION_INSTRUCTIONS) == 1
        assert client.params_of(CodexRequestMethod.THREAD_RESUME)
