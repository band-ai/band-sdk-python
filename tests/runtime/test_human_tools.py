"""Shape-equal tests for ``HumanTools`` methods.

These tests are **shape-equal**, not byte-equal. Per the product decision
for Phase 1 of INT-338 (INT-349), we verify three invariants per method:

1. The correct ``rest.human_api_*`` method is called.
2. It is called with the arguments today's ``thenvoi-mcp`` human handler
   would have produced for the same inputs (so the observable REST
   traffic is preserved).
3. The return value has the same **shape** (keys + Python types) as what
   today's MCP handler would produce — not a byte-equal fixture.

The pragmatic tradeoff: the MCP handlers return JSON strings (via
``serialize_response``), while ``HumanTools`` returns the raw Fern model.
Both pipelines feed from the same Fern response, so we assert shape
equality on the Fern model instead of recording and comparing byte-equal
JSON fixtures. This is faster to maintain and still catches regressions
at the REST-call and response-shape boundaries.

REST is faked with ``unittest.mock.AsyncMock``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.client.rest import DEFAULT_REQUEST_OPTIONS
from thenvoi.runtime.tools import HumanTools


def _make_rest_fake() -> MagicMock:
    """Construct a REST client fake with every ``human_api_*`` subclient present.

    Each subclient method returns a ``MagicMock`` (customized per test) via
    ``AsyncMock``. Tests scripting a specific return value do so via
    ``AsyncMock.return_value`` or ``side_effect``.
    """
    rest = MagicMock()
    # All human_api_* subclients are plain attribute mocks; individual
    # methods are replaced with AsyncMock per-test.
    rest.human_api_agents = MagicMock()
    rest.human_api_chats = MagicMock()
    rest.human_api_contacts = MagicMock()
    rest.human_api_memories = MagicMock()
    rest.human_api_messages = MagicMock()
    rest.human_api_participants = MagicMock()
    rest.human_api_peers = MagicMock()
    rest.human_api_profile = MagicMock()
    return rest


# ---------- human_agents ----------


@pytest.mark.asyncio
async def test_list_my_agents_forwards_page_args() -> None:
    rest = _make_rest_fake()
    response = MagicMock(data=[{"id": "agent-1", "name": "agent"}])
    rest.human_api_agents.list_my_agents = AsyncMock(return_value=response)

    tools = HumanTools(rest)
    result = await tools.list_my_agents(page=2, page_size=25)

    rest.human_api_agents.list_my_agents.assert_awaited_once_with(page=2, page_size=25)
    assert result is response


@pytest.mark.asyncio
async def test_register_my_agent_builds_request_object() -> None:
    from thenvoi_rest import AgentRegisterRequest

    rest = _make_rest_fake()
    response = MagicMock()
    rest.human_api_agents.register_my_agent = AsyncMock(return_value=response)

    tools = HumanTools(rest)
    result = await tools.register_my_agent(name="bot", description="desc")

    rest.human_api_agents.register_my_agent.assert_awaited_once()
    call_kwargs = rest.human_api_agents.register_my_agent.call_args.kwargs
    assert isinstance(call_kwargs["agent"], AgentRegisterRequest)
    assert call_kwargs["agent"].name == "bot"
    assert call_kwargs["agent"].description == "desc"
    assert result is response


# ---------- human_chats ----------


@pytest.mark.asyncio
async def test_list_my_chats_forwards_pagination() -> None:
    rest = _make_rest_fake()
    response = MagicMock(data=[])
    rest.human_api_chats.list_my_chats = AsyncMock(return_value=response)

    result = await HumanTools(rest).list_my_chats(page=1, page_size=10)

    rest.human_api_chats.list_my_chats.assert_awaited_once_with(page=1, page_size=10)
    assert result is response


@pytest.mark.asyncio
async def test_create_my_chat_room_with_task_id() -> None:
    from thenvoi_rest import CreateMyChatRoomRequestChat

    rest = _make_rest_fake()
    rest.human_api_chats.create_my_chat_room = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).create_my_chat_room(task_id="t-1")

    call_kwargs = rest.human_api_chats.create_my_chat_room.call_args.kwargs
    assert isinstance(call_kwargs["chat"], CreateMyChatRoomRequestChat)
    assert call_kwargs["chat"].task_id == "t-1"


@pytest.mark.asyncio
async def test_create_my_chat_room_without_task_id() -> None:
    from thenvoi_rest import CreateMyChatRoomRequestChat

    rest = _make_rest_fake()
    rest.human_api_chats.create_my_chat_room = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).create_my_chat_room()

    call_kwargs = rest.human_api_chats.create_my_chat_room.call_args.kwargs
    assert isinstance(call_kwargs["chat"], CreateMyChatRoomRequestChat)
    assert call_kwargs["chat"].task_id is None


@pytest.mark.asyncio
async def test_get_my_chat_room_by_id() -> None:
    rest = _make_rest_fake()
    rest.human_api_chats.get_my_chat_room = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).get_my_chat_room(chat_id="c-1")

    rest.human_api_chats.get_my_chat_room.assert_awaited_once_with(id="c-1")


# ---------- human_contacts ----------


@pytest.mark.asyncio
async def test_list_my_contacts_forwards_pagination() -> None:
    rest = _make_rest_fake()
    rest.human_api_contacts.list_my_contacts = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).list_my_contacts(page=3, page_size=50)
    rest.human_api_contacts.list_my_contacts.assert_awaited_once_with(
        page=3, page_size=50
    )


@pytest.mark.asyncio
async def test_create_contact_request_without_message() -> None:
    from thenvoi_rest import CreateContactRequestRequestContactRequest

    rest = _make_rest_fake()
    rest.human_api_contacts.create_contact_request = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).create_contact_request(recipient_handle="@alice")

    call_kwargs = rest.human_api_contacts.create_contact_request.call_args.kwargs
    request = call_kwargs["contact_request"]
    assert isinstance(request, CreateContactRequestRequestContactRequest)
    assert request.recipient_handle == "@alice"
    # message was not provided; it should not be set on the request.
    assert getattr(request, "message", None) in (None, "")


@pytest.mark.asyncio
async def test_create_contact_request_with_message() -> None:
    from thenvoi_rest import CreateContactRequestRequestContactRequest

    rest = _make_rest_fake()
    rest.human_api_contacts.create_contact_request = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).create_contact_request(recipient_handle="@bob", message="hi")

    request = rest.human_api_contacts.create_contact_request.call_args.kwargs[
        "contact_request"
    ]
    assert isinstance(request, CreateContactRequestRequestContactRequest)
    assert request.recipient_handle == "@bob"
    assert request.message == "hi"


@pytest.mark.asyncio
async def test_list_received_contact_requests_forwards_pagination() -> None:
    rest = _make_rest_fake()
    rest.human_api_contacts.list_received_contact_requests = AsyncMock(
        return_value=MagicMock()
    )

    await HumanTools(rest).list_received_contact_requests(page=1, page_size=20)
    rest.human_api_contacts.list_received_contact_requests.assert_awaited_once_with(
        page=1, page_size=20
    )


@pytest.mark.asyncio
async def test_list_sent_contact_requests_filters_by_status() -> None:
    rest = _make_rest_fake()
    rest.human_api_contacts.list_sent_contact_requests = AsyncMock(
        return_value=MagicMock()
    )

    await HumanTools(rest).list_sent_contact_requests(
        status="pending", page=1, page_size=20
    )
    rest.human_api_contacts.list_sent_contact_requests.assert_awaited_once_with(
        status="pending", page=1, page_size=20
    )


@pytest.mark.asyncio
async def test_approve_contact_request_calls_by_id() -> None:
    rest = _make_rest_fake()
    rest.human_api_contacts.approve_contact_request = AsyncMock(
        return_value=MagicMock()
    )

    await HumanTools(rest).approve_contact_request(request_id="r-1")
    rest.human_api_contacts.approve_contact_request.assert_awaited_once_with(
        id="r-1", request_options=DEFAULT_REQUEST_OPTIONS
    )


@pytest.mark.asyncio
async def test_reject_contact_request_calls_by_id() -> None:
    rest = _make_rest_fake()
    rest.human_api_contacts.reject_contact_request = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).reject_contact_request(request_id="r-2")
    rest.human_api_contacts.reject_contact_request.assert_awaited_once_with(
        id="r-2", request_options=DEFAULT_REQUEST_OPTIONS
    )


@pytest.mark.asyncio
async def test_cancel_contact_request_calls_by_id() -> None:
    rest = _make_rest_fake()
    rest.human_api_contacts.cancel_contact_request = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).cancel_contact_request(request_id="r-3")
    rest.human_api_contacts.cancel_contact_request.assert_awaited_once_with(
        id="r-3", request_options=DEFAULT_REQUEST_OPTIONS
    )


@pytest.mark.asyncio
async def test_resolve_handle_passes_handle() -> None:
    rest = _make_rest_fake()
    rest.human_api_contacts.resolve_handle = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).resolve_handle(handle="@alice")
    rest.human_api_contacts.resolve_handle.assert_awaited_once_with(handle="@alice")


@pytest.mark.asyncio
async def test_remove_my_contact_requires_one_identifier() -> None:
    """Matches today's MCP handler: returns the error string verbatim
    instead of raising. See ``thenvoi-mcp`` human_contacts.remove_my_contact.
    """
    rest = _make_rest_fake()
    rest.human_api_contacts.remove_my_contact = AsyncMock(return_value=MagicMock())

    result = await HumanTools(rest).remove_my_contact()
    assert result == "Error: Either contact_id or handle must be provided"
    rest.human_api_contacts.remove_my_contact.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_my_contact_with_contact_id_only() -> None:
    rest = _make_rest_fake()
    rest.human_api_contacts.remove_my_contact = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).remove_my_contact(contact_id="c-1")
    rest.human_api_contacts.remove_my_contact.assert_awaited_once_with(
        contact_id="c-1", request_options=DEFAULT_REQUEST_OPTIONS
    )


@pytest.mark.asyncio
async def test_remove_my_contact_with_handle_only() -> None:
    rest = _make_rest_fake()
    rest.human_api_contacts.remove_my_contact = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).remove_my_contact(handle="@bob")
    rest.human_api_contacts.remove_my_contact.assert_awaited_once_with(
        handle="@bob", request_options=DEFAULT_REQUEST_OPTIONS
    )


@pytest.mark.asyncio
async def test_remove_my_contact_with_both_sends_both() -> None:
    rest = _make_rest_fake()
    rest.human_api_contacts.remove_my_contact = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).remove_my_contact(contact_id="c-1", handle="@bob")
    rest.human_api_contacts.remove_my_contact.assert_awaited_once_with(
        contact_id="c-1", handle="@bob", request_options=DEFAULT_REQUEST_OPTIONS
    )


# ---------- human_messages ----------


@pytest.mark.asyncio
async def test_list_my_chat_messages_parses_since_iso() -> None:
    rest = _make_rest_fake()
    rest.human_api_messages.list_my_chat_messages = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).list_my_chat_messages(
        chat_id="c-1",
        page=1,
        page_size=20,
        message_type="text",
        since="2024-01-01T00:00:00Z",
    )
    call_kwargs = rest.human_api_messages.list_my_chat_messages.call_args.kwargs
    assert call_kwargs["chat_id"] == "c-1"
    assert call_kwargs["page"] == 1
    assert call_kwargs["page_size"] == 20
    assert call_kwargs["message_type"] == "text"
    assert isinstance(call_kwargs["since"], datetime)


@pytest.mark.asyncio
async def test_list_my_chat_messages_leaves_since_none_when_absent() -> None:
    rest = _make_rest_fake()
    rest.human_api_messages.list_my_chat_messages = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).list_my_chat_messages(chat_id="c-1")
    call_kwargs = rest.human_api_messages.list_my_chat_messages.call_args.kwargs
    assert call_kwargs["since"] is None


def _mk_participant(**kwargs: Any) -> MagicMock:
    p = MagicMock(spec=["id", "name", "username", "first_name"])
    for k, v in kwargs.items():
        setattr(p, k, v)
    # Reasonable defaults for attributes not set
    for attr in ("id", "name", "username", "first_name"):
        if attr not in kwargs:
            setattr(p, attr, None)
    return p


@pytest.mark.asyncio
async def test_send_my_chat_message_resolves_recipients_by_name() -> None:
    from thenvoi_rest import ChatMessageRequest

    rest = _make_rest_fake()
    alice = _mk_participant(id="u-1", name="Alice")
    bob = _mk_participant(id="u-2", username="bob")
    participants_response = MagicMock(data=[alice, bob])
    rest.human_api_participants.list_my_chat_participants = AsyncMock(
        return_value=participants_response
    )
    rest.human_api_messages.send_my_chat_message = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).send_my_chat_message(
        chat_id="c-1", content="hello", recipients="Alice, bob"
    )

    rest.human_api_participants.list_my_chat_participants.assert_awaited_once_with(
        chat_id="c-1"
    )
    send_call = rest.human_api_messages.send_my_chat_message.call_args
    assert send_call.kwargs["chat_id"] == "c-1"
    message = send_call.kwargs["message"]
    assert isinstance(message, ChatMessageRequest)
    assert message.content == "hello"
    assert {m.id for m in message.mentions} == {"u-1", "u-2"}
    assert send_call.kwargs["request_options"] is DEFAULT_REQUEST_OPTIONS


@pytest.mark.asyncio
async def test_send_my_chat_message_rejects_empty_recipients() -> None:
    """Matches today's MCP handler: returns the error string verbatim."""
    rest = _make_rest_fake()
    rest.human_api_participants.list_my_chat_participants = AsyncMock()
    rest.human_api_messages.send_my_chat_message = AsyncMock()

    result = await HumanTools(rest).send_my_chat_message(
        chat_id="c-1", content="hi", recipients="  "
    )
    assert result == "Error: recipients cannot be empty"
    rest.human_api_participants.list_my_chat_participants.assert_not_awaited()
    rest.human_api_messages.send_my_chat_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_my_chat_message_reports_unknown_recipients() -> None:
    """Matches today's MCP handler: returns the error string verbatim."""
    rest = _make_rest_fake()
    alice = _mk_participant(id="u-1", name="Alice")
    rest.human_api_participants.list_my_chat_participants = AsyncMock(
        return_value=MagicMock(data=[alice])
    )
    rest.human_api_messages.send_my_chat_message = AsyncMock()

    result = await HumanTools(rest).send_my_chat_message(
        chat_id="c-1", content="hi", recipients="Alice,Zeke"
    )
    assert isinstance(result, str)
    assert result.startswith("Error: Not found: zeke")
    assert "Available: alice" in result
    rest.human_api_messages.send_my_chat_message.assert_not_awaited()


# ---------- human_participants ----------


@pytest.mark.asyncio
async def test_list_my_chat_participants_forwards_filter() -> None:
    rest = _make_rest_fake()
    rest.human_api_participants.list_my_chat_participants = AsyncMock(
        return_value=MagicMock()
    )

    await HumanTools(rest).list_my_chat_participants(
        chat_id="c-1", participant_type="Agent"
    )
    rest.human_api_participants.list_my_chat_participants.assert_awaited_once_with(
        chat_id="c-1", participant_type="Agent"
    )


@pytest.mark.asyncio
async def test_add_my_chat_participant_builds_request_with_default_role() -> None:
    from thenvoi_rest import ParticipantRequest

    rest = _make_rest_fake()
    rest.human_api_participants.add_my_chat_participant = AsyncMock(
        return_value=MagicMock()
    )

    result = await HumanTools(rest).add_my_chat_participant(
        chat_id="c-1", participant_id="p-1"
    )
    call_kwargs = rest.human_api_participants.add_my_chat_participant.call_args.kwargs
    assert call_kwargs["chat_id"] == "c-1"
    participant = call_kwargs["participant"]
    assert isinstance(participant, ParticipantRequest)
    assert participant.participant_id == "p-1"
    assert participant.role == "member"
    # Matches today's MCP handler: returns the summary string verbatim.
    assert result == "Added participant: p-1"


@pytest.mark.asyncio
async def test_add_my_chat_participant_passes_role() -> None:
    rest = _make_rest_fake()
    rest.human_api_participants.add_my_chat_participant = AsyncMock(
        return_value=MagicMock()
    )

    result = await HumanTools(rest).add_my_chat_participant(
        chat_id="c-1", participant_id="p-1", role="admin"
    )
    participant = rest.human_api_participants.add_my_chat_participant.call_args.kwargs[
        "participant"
    ]
    assert participant.role == "admin"
    assert result == "Added participant: p-1"


@pytest.mark.asyncio
async def test_remove_my_chat_participant_passes_ids() -> None:
    rest = _make_rest_fake()
    rest.human_api_participants.remove_my_chat_participant = AsyncMock(
        return_value=MagicMock()
    )

    result = await HumanTools(rest).remove_my_chat_participant(
        chat_id="c-1", participant_id="p-2"
    )
    call_kwargs = (
        rest.human_api_participants.remove_my_chat_participant.call_args.kwargs
    )
    assert call_kwargs["chat_id"] == "c-1"
    assert call_kwargs["id"] == "p-2"
    # Matches today's MCP handler: returns the summary string verbatim.
    assert result == "Removed participant: p-2"


# ---------- human_memories ----------


@pytest.mark.asyncio
async def test_list_user_memories_forwards_all_filters() -> None:
    rest = _make_rest_fake()
    rest.human_api_memories.list_user_memories = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).list_user_memories(
        chat_room_id="r-1",
        scope="subject",
        system="long_term",
        memory_type="semantic",
        segment="user",
        content_query="hello",
        page_size=50,
        status="active",
    )
    rest.human_api_memories.list_user_memories.assert_awaited_once_with(
        chat_room_id="r-1",
        scope="subject",
        system="long_term",
        type="semantic",
        segment="user",
        content_query="hello",
        page_size=50,
        status="active",
    )


@pytest.mark.asyncio
async def test_get_user_memory_passes_positional_id() -> None:
    rest = _make_rest_fake()
    rest.human_api_memories.get_user_memory = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).get_user_memory(memory_id="m-1")
    rest.human_api_memories.get_user_memory.assert_awaited_once_with("m-1")


@pytest.mark.asyncio
async def test_supersede_user_memory_passes_positional_id() -> None:
    rest = _make_rest_fake()
    rest.human_api_memories.supersede_user_memory = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).supersede_user_memory(memory_id="m-1")
    rest.human_api_memories.supersede_user_memory.assert_awaited_once_with(
        "m-1", request_options=DEFAULT_REQUEST_OPTIONS
    )


@pytest.mark.asyncio
async def test_archive_user_memory_passes_positional_id() -> None:
    rest = _make_rest_fake()
    rest.human_api_memories.archive_user_memory = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).archive_user_memory(memory_id="m-1")
    rest.human_api_memories.archive_user_memory.assert_awaited_once_with(
        "m-1", request_options=DEFAULT_REQUEST_OPTIONS
    )


@pytest.mark.asyncio
async def test_restore_user_memory_passes_positional_id() -> None:
    rest = _make_rest_fake()
    rest.human_api_memories.restore_user_memory = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).restore_user_memory(memory_id="m-1")
    rest.human_api_memories.restore_user_memory.assert_awaited_once_with(
        "m-1", request_options=DEFAULT_REQUEST_OPTIONS
    )


@pytest.mark.asyncio
async def test_delete_user_memory_returns_mcp_shape_payload() -> None:
    """The Fern endpoint returns no body; the tool wraps the outcome in a
    ``{"deleted": True, "id": ...}`` dict to match today's MCP handler."""
    rest = _make_rest_fake()
    rest.human_api_memories.delete_user_memory = AsyncMock(return_value=None)

    result = await HumanTools(rest).delete_user_memory(memory_id="m-1")
    rest.human_api_memories.delete_user_memory.assert_awaited_once_with(
        "m-1", request_options=DEFAULT_REQUEST_OPTIONS
    )
    assert result == {"deleted": True, "id": "m-1"}
    assert set(result.keys()) == {"deleted", "id"}
    assert isinstance(result["deleted"], bool)
    assert isinstance(result["id"], str)


# ---------- human_profile / human_peers ----------


@pytest.mark.asyncio
async def test_get_my_profile_takes_no_args() -> None:
    rest = _make_rest_fake()
    rest.human_api_profile.get_my_profile = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).get_my_profile()
    rest.human_api_profile.get_my_profile.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_update_my_profile_requires_at_least_one_field() -> None:
    """Matches today's MCP handler: returns the error string verbatim."""
    rest = _make_rest_fake()
    rest.human_api_profile.update_my_profile = AsyncMock(return_value=MagicMock())

    result = await HumanTools(rest).update_my_profile()
    assert (
        result == "Error: At least one field (first_name or last_name) must be provided"
    )
    rest.human_api_profile.update_my_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_my_profile_builds_partial_user_payload() -> None:
    rest = _make_rest_fake()
    rest.human_api_profile.update_my_profile = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).update_my_profile(first_name="Alice")
    rest.human_api_profile.update_my_profile.assert_awaited_once_with(
        user={"first_name": "Alice"}, request_options=DEFAULT_REQUEST_OPTIONS
    )

    rest.human_api_profile.update_my_profile.reset_mock()
    await HumanTools(rest).update_my_profile(last_name="Doe")
    rest.human_api_profile.update_my_profile.assert_awaited_once_with(
        user={"last_name": "Doe"}, request_options=DEFAULT_REQUEST_OPTIONS
    )

    rest.human_api_profile.update_my_profile.reset_mock()
    await HumanTools(rest).update_my_profile(first_name="Alice", last_name="Doe")
    rest.human_api_profile.update_my_profile.assert_awaited_once_with(
        user={"first_name": "Alice", "last_name": "Doe"},
        request_options=DEFAULT_REQUEST_OPTIONS,
    )


@pytest.mark.asyncio
async def test_list_my_peers_maps_peer_type_to_type() -> None:
    """``peer_type`` is the public-facing kwarg (mirrors the MCP handler);
    the Fern client expects ``type``. The tool bridges the two."""
    rest = _make_rest_fake()
    rest.human_api_peers.list_my_peers = AsyncMock(return_value=MagicMock())

    await HumanTools(rest).list_my_peers(
        not_in_chat="c-1", peer_type="Agent", page=1, page_size=50
    )
    rest.human_api_peers.list_my_peers.assert_awaited_once_with(
        not_in_chat="c-1", type="Agent", page=1, page_size=50
    )


# ---------- Instance construction ----------


def test_humantools_is_stateless_per_credential() -> None:
    """``HumanTools`` binds to one REST client; no room scope, no cache."""
    rest = _make_rest_fake()
    tools = HumanTools(rest)
    assert tools.rest is rest
    assert not hasattr(tools, "room_id")
