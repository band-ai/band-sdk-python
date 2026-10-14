"""Replaying a room's history into a fresh Parlant session."""

from __future__ import annotations

import pytest

from band.adapters.parlant.history import complete_exchanges, inject_history
from band.converters.parlant import ParlantMessages, ParlantRole
from tests.adapters.parlant.helpers import BAND_NAME, SENDER_NAME, SESSION_ID

HISTORY: ParlantMessages = [
    {"role": ParlantRole.USER, "content": "Hello", "sender": SENDER_NAME},
    {"role": ParlantRole.ASSISTANT, "content": "Hi there!", "sender": BAND_NAME},
    {"role": ParlantRole.USER, "content": "Pending question"},
]


def test_complete_exchanges_drop_the_unanswered_question():
    assert complete_exchanges(HISTORY) == HISTORY[:2]


@pytest.mark.usefixtures("parlant_sessions")
async def test_injects_complete_exchanges_only(mock_app):
    count = await inject_history(
        app=mock_app, session_id=SESSION_ID, history=HISTORY, agent_name=BAND_NAME
    )

    assert count == 2
    assert [
        c.kwargs["message"]
        for c in mock_app.sessions.create_customer_message.await_args_list
    ] == ["Hello"]
    assert [
        c.kwargs["data"]["message"]
        for c in mock_app.sessions.create_event.await_args_list
    ] == ["Hi there!"]


async def test_handles_empty_history(mock_app):
    count = await inject_history(
        app=mock_app, session_id=SESSION_ID, history=[], agent_name=BAND_NAME
    )

    assert count == 0
