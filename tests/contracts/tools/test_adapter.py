"""Contract tools execute through the existing Anthropic adapter tool path."""

from __future__ import annotations

import json
from typing import cast

from anthropic.types import Message, ToolUseBlock, Usage

from band.adapters.anthropic import AnthropicAdapter, AnthropicAdapterConfig
from band.contracts.client import ContractClient
from band.contracts.tools import contract_tools
from band.core.protocols import AgentToolsProtocol
from band.testing.fake_tools import FakeAgentTools
from tests.contracts.client.kit import RoomPeer


class TestAdapter:
    async def test_only_an_accepted_proposal_counts_as_work_in_the_existing_adapter(
        self,
    ) -> None:
        peer = RoomPeer()
        async with (
            peer.client() as http,
            ContractClient(peer.connection(), http=http) as client,
        ):
            room = await client.room("room", "session", actor=peer.actor)
            adapter = AnthropicAdapter(
                AnthropicAdapterConfig(provider_key="test"),
                additional_tools=contract_tools(room),
            )
            response = Message(
                id="response",
                model="test",
                role="assistant",
                type="message",
                stop_reason="tool_use",
                usage=Usage(input_tokens=1, output_tokens=1),
                content=[
                    ToolUseBlock(
                        id="tool",
                        type="tool_use",
                        name="contractpropose",
                        input={
                            "id": "stable",
                            "kind": "question",
                            "payload": {"text": "hello"},
                            "expected_version": 1,
                        },
                    )
                ],
            )
            tools = FakeAgentTools()
            adapter_tools = cast(AgentToolsProtocol, tools)
            peer.reject = True
            rejected, _ = await adapter._process_tool_calls(response, adapter_tools)
            refusal = json.loads(rejected[0]["content"])
            assert (refusal["ok"], refusal["status"]) == (False, "rejected")
            assert (refusal["reason"], refusal["detail"]) == (
                "not_an_opening",
                {"kind": "question", "opening_kinds": ["assign"]},
            )
            assert not tools.turn.complete
            peer.reject = False
            accepted, _ = await adapter._process_tool_calls(response, adapter_tools)
            assert accepted[0]["is_error"] is False
            assert tools.turn.complete
            await adapter.client.close()
        assert len(peer.proposals) == 2
