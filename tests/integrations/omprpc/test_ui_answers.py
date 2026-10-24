"""Structured UI answers reach the native peer without becoming cancellation."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import JsonValue

from band.integrations.ompwire import (
    OmpEventType,
    OmpFrameType,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.integrations.omprpc.kit import ClientFactory, Observer


class TestStructuredUIAnswers:
    @pytest.mark.parametrize(
        "answer",
        [
            {"id": "database-choice", "selectedOptions": []},
            {
                "id": "database-choice",
                "selectedOptions": [],
                "customInput": "Use the existing database",
            },
        ],
        ids=["empty-selection", "custom-input"],
    )
    async def test_answer_variant_survives_callback_to_wire(
        self, client_factory: ClientFactory, answer: dict[str, JsonValue]
    ) -> None:
        client = await client_factory("event-backpressure")
        observer = Observer()

        async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
            assert request.method == "ask"
            assert request.questions[0].id == answer["id"]
            return OmpUIResponse.model_validate({"answers": [answer]})

        running = asyncio.create_task(
            client.prompt(
                "first",
                on_event=observer.on_event,
                on_tool=observer.on_tool,
                on_ui=on_ui,
            )
        )
        try:
            async with asyncio.timeout(8):
                await observer.wait_for(OmpEventType.AGENT_START)
                receipt = await client.request("ask-ui")
                assert receipt == {
                    "answer": {
                        "type": OmpFrameType.UI_RESPONSE,
                        "id": "structured-question",
                        "answers": [answer],
                    }
                }
                await client.request("event-result")
                await running
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
