from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from band.client.rest import AsyncRestClient
    from band.platform.link import BandLink


def assert_rest_pattern_methods_exist(link: BandLink) -> None:
    """Assert the documented Fern namespace methods exist on a real link."""
    assert inspect.iscoroutinefunction(link.rest.agent_api_chats.create_agent_chat)
    assert inspect.iscoroutinefunction(
        link.rest.agent_api_messages.create_agent_chat_message
    )
    assert inspect.iscoroutinefunction(
        link.rest.agent_api_participants.list_agent_chat_participants
    )


def assert_contact_respond_method_exists(client: AsyncRestClient) -> None:
    """Assert the documented contact response method exists on a real client."""
    assert inspect.iscoroutinefunction(
        client.agent_api_contacts.respond_to_agent_contact_request
    )


def assert_omit_vs_null_calls(client: AsyncRestClient) -> None:
    """Assert the markdown snippet demonstrated null vs omitted Fern fields."""
    calls = client._markdown_captured_json
    assert calls[0]["handle"] is None
    assert calls[1]["handle"] is Ellipsis  # Fern OMIT sentinel, not sent as null
