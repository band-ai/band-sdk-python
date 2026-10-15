"""The customer message names this agent the way Parlant's prompt does."""

from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from band.adapters.parlant import ParlantAdapterConfig
from band.adapters.parlant.messages import name_this_agent
from band.testing import FakeAgentTools
from tests.adapters.parlant.helpers import BAND_NAME, SENDER_NAME
from tests.testing.support import seeded_participant

AGENT_ID = "agent-1"
HANDLE = "owner/showcase"
OTHER_HANDLE = "owner/showcase-2"
PARLANT_NAME = "E2E Showcase Agent"

pytestmark = pytest.mark.usefixtures("parlant_sessions")


class TestCustomerAddress:
    async def test_posts_the_parlant_name_in_place_of_this_agents_handle(
        self, start_adapter, mock_app, sample_message, caplog
    ) -> None:
        tools = FakeAgentTools(
            agent_id=AGENT_ID,
            room_id=sample_message.room_id,
            participants=[
                seeded_participant(
                    AGENT_ID, handle=HANDLE, name="showcase", type="Agent"
                ),
                seeded_participant(
                    "other", handle=OTHER_HANDLE, name="other", type="Agent"
                ),
            ],
        )
        message = replace(
            sample_message,
            content=f"@{HANDLE} look up the code and tell @{OTHER_HANDLE}",
        )
        adapter = await start_adapter(
            ParlantAdapterConfig(
                name=PARLANT_NAME, response_timeout=0.05, response_poll=0.01
            )
        )

        with caplog.at_level(logging.INFO, logger="band.adapters.parlant.adapter"):
            await adapter.on_message(
                msg=message,
                tools=tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id=message.room_id,
            )

        posted = mock_app.sessions.create_customer_message.await_args.kwargs["message"]
        assert (
            posted
            == f"[{SENDER_NAME}]: {PARLANT_NAME} look up the code and tell @{OTHER_HANDLE}"
        )
        assert (
            f"Room {message.room_id}: posting to Parlant (agent {PARLANT_NAME}): {posted}"
            in caplog.messages
        )

    async def test_posts_the_band_name_when_parlant_has_no_own_name(
        self, start_adapter, mock_app, sample_message
    ) -> None:
        tools = FakeAgentTools(
            agent_id=AGENT_ID,
            room_id=sample_message.room_id,
            participants=[
                seeded_participant(
                    AGENT_ID, handle=HANDLE, name="showcase", type="Agent"
                )
            ],
        )
        message = replace(sample_message, content=f"@{HANDLE} look up the code")
        adapter = await start_adapter()

        await adapter.on_message(
            msg=message,
            tools=tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=message.room_id,
        )

        posted = mock_app.sessions.create_customer_message.await_args.kwargs["message"]
        assert posted == f"[{SENDER_NAME}]: {BAND_NAME} look up the code"

    def test_rewrites_a_raw_id_and_a_collapsed_name(self) -> None:
        participants = [{"id": AGENT_ID, "name": "Tool Agent"}]
        raw = name_this_agent(
            f"@[[{AGENT_ID}]] look up the code",
            agent_id=AGENT_ID,
            agent_name=PARLANT_NAME,
            participants=participants,
        )
        collapsed = name_this_agent(
            "@Tool-Agent look up the code",
            agent_id=AGENT_ID,
            agent_name=PARLANT_NAME,
            participants=participants,
        )

        assert raw == f"{PARLANT_NAME} look up the code"
        assert collapsed == f"{PARLANT_NAME} look up the code"
