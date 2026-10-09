"""Tests for the Parlant contact tools, driven against ``FakeAgentTools``."""

from __future__ import annotations

import pytest

from band.core.types import ContactRequestAction
from band.integrations.parlant.tools import set_session_tools
from band.testing import FakeAgentTools
from tests.testing.support import seeded_contact

pytest.importorskip("parlant.sdk")  # real @p.tool schemas; dev-parlant venv only


@pytest.mark.parametrize(
    ("contacts", "reported"),
    [
        ([], "pending"),
        ([seeded_contact("bob-id", handle="bob", name="Bob")], "approved"),
    ],
    ids=["new-request", "already-a-contact"],
)
async def test_add_contact_reports_the_requests_status(
    parlant_tools, mock_context, contacts, reported
):
    set_session_tools(mock_context.session_id, FakeAgentTools(contacts=contacts))

    add_contact = parlant_tools["band_add_contact"]
    result = await add_contact(mock_context, "bob")

    assert result.data == f"Contact request to bob: {reported}"


async def test_invalid_contact_action_is_refused_before_any_request(
    parlant_tools, mock_context
):
    """FakeAgentTools rejects an unknown action with its own error, so this
    text proves the tool refused before calling it."""
    set_session_tools(mock_context.session_id, FakeAgentTools())

    respond = parlant_tools["band_respond_contact_request"]
    result = await respond(mock_context, "ignore", handle="bob")

    choices = ", ".join(f"'{action}'" for action in ContactRequestAction)
    assert result.data == f"Error: Invalid action 'ignore'. Use one of {choices}"
