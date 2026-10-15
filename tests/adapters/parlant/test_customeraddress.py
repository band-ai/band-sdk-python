"""The customer message names this agent the way Parlant's prompt does."""

from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from band.adapters.parlant import ParlantAdapter, ParlantAdapterConfig
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

        with caplog.at_level(logging.INFO, logger=ParlantAdapter.__module__):
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

    async def test_posts_the_live_agent_name(
        self, start_adapter, mock_app, mock_parlant_agent, sample_message
    ) -> None:
        mock_parlant_agent.name = "Live Prompt Name"
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
        adapter = await start_adapter(
            ParlantAdapterConfig(
                name=PARLANT_NAME, response_timeout=0.05, response_poll=0.01
            )
        )

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
        assert posted == f"[{SENDER_NAME}]: Live Prompt Name look up the code"

    async def test_rewrites_this_agent_in_bootstrap_history(
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
        message = replace(sample_message, content="look this up")
        adapter = await start_adapter(
            ParlantAdapterConfig(
                name=PARLANT_NAME, response_timeout=0.05, response_poll=0.01
            )
        )

        await adapter.on_message(
            msg=message,
            tools=tools,
            history=[
                {
                    "role": "user",
                    "content": f"[{SENDER_NAME}]: @{HANDLE} what is the code",
                    "sender": SENDER_NAME,
                },
                {"role": "assistant", "content": "Noted.", "sender": BAND_NAME},
            ],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=message.room_id,
        )

        posted = [
            call.kwargs["message"]
            for call in mock_app.sessions.create_customer_message.await_args_list
        ]
        assert posted == [
            f"[{SENDER_NAME}]: {PARLANT_NAME} what is the code",
            f"[{SENDER_NAME}]: look this up",
        ]

    def test_leaves_another_participants_handle(self) -> None:
        participants = [
            {"id": AGENT_ID, "handle": HANDLE, "name": "showcase"},
            {"id": "other", "handle": "showcase", "name": "other"},
        ]

        rewritten = name_this_agent(
            f"@{HANDLE} ask @showcase",
            agent_id=AGENT_ID,
            agent_name=PARLANT_NAME,
            participants=participants,
        )

        assert rewritten == f"{PARLANT_NAME} ask @showcase"

    def test_rewrites_a_mention_before_punctuation_but_not_a_longer_handle(
        self,
    ) -> None:
        participants = [{"id": AGENT_ID, "handle": HANDLE, "name": "showcase"}]

        punctuated = name_this_agent(
            f"@{HANDLE}, look this up",
            agent_id=AGENT_ID,
            agent_name=PARLANT_NAME,
            participants=participants,
        )
        longer = name_this_agent(
            f"@{OTHER_HANDLE} look this up",
            agent_id=AGENT_ID,
            agent_name=PARLANT_NAME,
            participants=participants,
        )

        assert punctuated == f"{PARLANT_NAME}, look this up"
        assert longer == f"@{OTHER_HANDLE} look this up"

    def test_leaves_a_token_that_only_ends_in_the_mention(self) -> None:
        participants = [{"id": AGENT_ID, "handle": HANDLE, "name": "showcase"}]

        rewritten = name_this_agent(
            f"ping a@{HANDLE} now",
            agent_id=AGENT_ID,
            agent_name=PARLANT_NAME,
            participants=participants,
        )

        assert rewritten == f"ping a@{HANDLE} now"

    def test_inserts_a_name_that_contains_a_backslash(self) -> None:
        participants = [{"id": AGENT_ID, "handle": HANDLE, "name": "showcase"}]

        rewritten = name_this_agent(
            f"@{HANDLE} hi",
            agent_id=AGENT_ID,
            agent_name="Acme\\South",
            participants=participants,
        )

        assert rewritten == "Acme\\South hi"
