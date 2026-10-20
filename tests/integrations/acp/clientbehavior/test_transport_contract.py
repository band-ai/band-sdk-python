from __future__ import annotations

from typing import Any

import pytest

from band.adapters.copilot_acp import CopilotACPAdapter
from band.adapters.cursor_acp import CursorACPAdapter
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
)
from band.runtime.prompts import (
    COMMUNICATION_INSTRUCTIONS,
    TURN_COMPLETION_GUIDANCE,
)
from band.runtime.tools import BandTool
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
    started_acp_adapter,
)
from tests.integrations.acp.clientbehavior.kit import rehydration_history


class TestTransportContract:
    @pytest.mark.parametrize("adapter_class", [CopilotACPAdapter, CursorACPAdapter])
    @pytest.mark.parametrize(
        "selection, expected",
        [
            (
                {
                    "include_tools": {
                        BandTool.SEND_MESSAGE,
                        BandTool.NO_REPLY,
                        BandTool.GET_PARTICIPANTS,
                    }
                },
                {BandTool.SEND_MESSAGE, BandTool.NO_REPLY, BandTool.GET_PARTICIPANTS},
            ),
            (
                {
                    "include_categories": {"chat"},
                    "include_tools": {
                        BandTool.SEND_MESSAGE,
                        BandTool.NO_REPLY,
                        BandTool.GET_PARTICIPANTS,
                    },
                    "exclude_tools": {BandTool.SEND_MESSAGE, BandTool.NO_REPLY},
                },
                {BandTool.GET_PARTICIPANTS},
            ),
        ],
    )
    async def test_acp_wrappers_advertise_filtered_platform_tools(
        self,
        adapter_class: type[ACPClientAdapter],
        selection: dict[str, Any],
        expected: set[str],
    ) -> None:
        agent = FakeACPAgent()
        advertised: set[str] = set()

        @agent.on_prompt
        async def inspect_tools(peer: FakeACPAgent, session_id: str) -> None:
            advertised.update(
                tool.name
                for tool in await peer.list_mcp_tools(
                    session_id=session_id, server="band"
                )
            )

        adapter = adapter_class(**selection)
        async with started_acp_adapter(adapter, agent) as session:
            await session.send("inspect registered tools")
        assert advertised == expected

    @pytest.mark.parametrize("restored", [True, False])
    async def test_acp_mandatory_transport_contract_refreshes_first_prompt(
        self,
        restored: bool,
    ) -> None:
        agent = FakeACPAgent(supports_session_load=True).will_say("native")
        history = (
            rehydration_history("old message", session="old-session")
            if restored
            else []
        )
        async with acp_adapter(
            agent, fake_agent_config(custom_section="custom instructions")
        ) as session:
            await session.send("new message", history=history)
        assert agent.prompt_texts()[0].count(COMMUNICATION_INSTRUCTIONS) == 1
        assert agent.prompt_texts()[0].count(TURN_COMPLETION_GUIDANCE) == 1
