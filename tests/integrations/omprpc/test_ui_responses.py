"""Preserve native UI answer variants through the public prompt callback."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import JsonValue

from band.integrations.ompwire import OmpFrameType, OmpUIRequest, OmpUIResponse
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.callbacks import echo_tool
from tests.integrations.omprpc.events import EventRecorder, follow_up_texts
from tests.integrations.omprpc.spawn import ClientFactory
from tests.integrations.omprpc.wirelog import checkpoint_frames
from tests.integrations.ompscenarios import PeerScenario


class TestUIResponseVariants:
    @pytest.mark.parametrize(
        ("response", "answer"),
        [
            pytest.param(OmpUIResponse(value=""), {"value": ""}, id="empty-text"),
            pytest.param(
                OmpUIResponse(confirmed=False),
                {"confirmed": False},
                id="declined-confirmation",
            ),
            pytest.param(
                OmpUIResponse(cancelled=False), {}, id="unanswered-not-cancelled"
            ),
            pytest.param(
                OmpUIResponse(cancelled=True),
                {"cancelled": True},
                id="explicit-cancellation",
            ),
        ],
    )
    async def test_callback_answer_reaches_the_peer_without_other_variants(
        self,
        client_factory: ClientFactory,
        response: OmpUIResponse,
        answer: dict[str, JsonValue],
    ) -> None:
        client = await client_factory(PeerScenario.PARALLEL_CALLBACKS)
        observer = EventRecorder()

        async def on_ui(_request: OmpUIRequest) -> OmpUIResponse:
            return response

        async with asyncio.timeout(5):
            await client.prompt(
                "first",
                on_event=observer.on_event,
                on_tool=echo_tool,
                on_ui=on_ui,
            )
            frames = await checkpoint_frames(client)
        reply = next(frame for frame in frames if frame.get("id") == "approval-1")
        assert reply == {
            "type": OmpFrameType.UI_RESPONSE,
            "id": "approval-1",
            **answer,
        }
        assert await follow_up_texts(client) == [COMPLETE]
