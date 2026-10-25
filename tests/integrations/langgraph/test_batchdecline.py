"""A successful decline ends the native graph for a batch turn too."""

from __future__ import annotations

from dataclasses import replace

from langchain_core.messages import AIMessage

from band.adapters.langgraph import LangGraphAdapter
from band.runtime.tools import BandTool
from tests.framework_conformance.batchprobes import BATCH, delivery_input
from tests.integrations.langgraph.declinekit import DeclineTools, ScriptedModel


class TestBatchDecline:
    async def test_declining_a_batch_ends_the_turn_without_another_model_request(
        self,
    ) -> None:
        model = ScriptedModel(
            messages=iter(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": BandTool.NO_REPLY,
                                "args": {"reason": "FYI"},
                                "id": "decline",
                            }
                        ],
                    ),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": BandTool.SEND_MESSAGE,
                                "args": {
                                    "content": "unwanted reply",
                                    "mentions": [
                                        "00000000-0000-0000-0000-000000000001"
                                    ],
                                },
                                "id": "reply",
                            }
                        ],
                    ),
                    AIMessage(content="done"),
                ]
            ),
            disable_streaming=True,
        )
        adapter = LangGraphAdapter(llm=model)
        await adapter.on_started("Bot", "Bot")
        tools = DeclineTools()
        inp = replace(await delivery_input(BATCH), tools=tools)

        await adapter.on_messages(inp)

        assert len(inp.messages) == len(BATCH)
        assert model.requests == 1
        assert tools.turn.replied
        assert tools.replies == []
