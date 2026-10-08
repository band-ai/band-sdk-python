"""Replaying a room's history into a fresh Parlant session."""

from __future__ import annotations

import pytest

from band.adapters.parlant.history import complete_exchanges, inject_history

HISTORY = [
    {"role": "user", "content": "Hello", "sender": "Alice"},
    {"role": "assistant", "content": "Hi there!", "sender": "TestBot"},
    {"role": "user", "content": "Pending question"},
]


def test_complete_exchanges_drop_the_unanswered_question():
    assert complete_exchanges(HISTORY) == HISTORY[:2]


@pytest.mark.usefixtures("parlant_sessions")
async def test_injects_complete_exchanges_only(mock_app):
    count = await inject_history(
        app=mock_app, session_id="session-123", history=HISTORY, agent_name="TestBot"
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
        app=mock_app, session_id="session-123", history=[], agent_name="TestBot"
    )

    assert count == 0
