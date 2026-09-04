"""Pytest configuration for band-mcp tests.

Fixtures from band-testing-python are auto-loaded via pytest entry point.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

import pytest
from band_rest import RestClient
from mcp import ClientSession


def _assert_no_method_name_collisions() -> None:
    """Verify method names are unique within agent and human namespace groups.

    Several tests in this suite map every ``agent_api_*`` namespace onto one
    shared mock (and every ``human_api_*`` namespace onto another) rather
    than spec'ing each namespace separately. If two namespaces in the same
    group ever share a method name, such a test would silently pass with the
    wrong assertion instead of failing loudly.
    """
    try:
        client = RestClient(api_key="dummy", base_url="http://localhost")
    except Exception as exc:
        raise AssertionError(
            f"Could not instantiate RestClient for collision check: {exc}"
        ) from exc

    for prefix in ("agent_api_", "human_api_"):
        method_to_namespace: dict[str, str] = {}
        for attr_name in dir(client):
            if not attr_name.startswith(prefix):
                continue
            obj = getattr(client, attr_name)
            methods = [
                m
                for m in dir(obj)
                if not m.startswith("_") and callable(getattr(obj, m))
            ]
            for method in methods:
                if method in method_to_namespace:
                    raise AssertionError(
                        f"Method name collision: '{method}' exists in both "
                        f"'{method_to_namespace[method]}' and '{attr_name}'. "
                        f"The shared mock strategy in conftest.py is no longer safe. "
                        f"Split into per-namespace mock objects."
                    )
                method_to_namespace[method] = attr_name


@pytest.fixture(scope="session", autouse=True)
def _check_mock_safety() -> None:
    """Session-scoped guard against mock method-name collisions."""
    _assert_no_method_name_collisions()


class FakeHumanTools:
    """Fake implementation of the ``HumanTools`` surface for testing.

    Mirrors ``band.testing.FakeAgentTools``' style (behavioral fake with
    observable state, not a ``MagicMock``) for the human surface. Test-local
    for now -- promote to ``band.testing`` only if a second consumer appears.

    Returns plain dicts, not exact Fern models: unlike ``FakeAgentTools``
    (whose peers/contacts/memories back real adapter assertions today),
    nothing yet asserts on Fern-specific shape for the human surface. Widen
    to real Fern models if a consumer needs that fidelity.
    """

    def __init__(
        self,
        *,
        agents: list[dict[str, Any]] | None = None,
        chats: list[dict[str, Any]] | None = None,
        contacts: list[dict[str, Any]] | None = None,
        peers: list[dict[str, Any]] | None = None,
        memories: list[dict[str, Any]] | None = None,
        chat_participants: dict[str, list[dict[str, Any]]] | None = None,
        profile: dict[str, Any] | None = None,
    ) -> None:
        self._agents: list[dict[str, Any]] = list(agents or [])
        self._chats: dict[str, dict[str, Any]] = {c["id"]: c for c in (chats or [])}
        self._contacts: list[dict[str, Any]] = list(contacts or [])
        self._peers: list[dict[str, Any]] = list(peers or [])
        self.memories: list[dict[str, Any]] = list(memories or [])
        self._chat_participants: dict[str, list[dict[str, Any]]] = {
            chat_id: list(participants)
            for chat_id, participants in (chat_participants or {}).items()
        }
        self._profile: dict[str, Any] = dict(
            profile or {"id": "user-fake", "first_name": "Test", "last_name": "User"}
        )

        self.messages_sent: list[dict[str, Any]] = []
        self.contact_requests_created: list[dict[str, Any]] = []
        self.contact_requests_responded: list[dict[str, Any]] = []
        self.participants_added: list[dict[str, Any]] = []
        self.participants_removed: list[dict[str, Any]] = []

    # --- agents ---

    async def list_my_agents(
        self, page: int | None = None, page_size: int | None = None
    ) -> dict[str, Any]:
        return {"data": list(self._agents)}

    async def register_my_agent(self, name: str, description: str) -> dict[str, Any]:
        agent = {"id": str(uuid.uuid4()), "name": name, "description": description}
        self._agents.append(agent)
        return agent

    # --- chats ---

    async def list_my_chats(
        self, page: int | None = None, page_size: int | None = None
    ) -> dict[str, Any]:
        return {"data": list(self._chats.values())}

    async def create_my_chat_room(self, task_id: str | None = None) -> dict[str, Any]:
        chat = {"id": f"chat-{uuid.uuid4()}", "task_id": task_id}
        self._chats[chat["id"]] = chat
        return chat

    async def get_my_chat_room(self, chat_id: str) -> dict[str, Any]:
        chat = self._chats.get(chat_id)
        if chat is None:
            raise RuntimeError(f"chat room not found: {chat_id}")
        return chat

    # --- contacts ---

    async def list_my_contacts(
        self, page: int | None = None, page_size: int | None = None
    ) -> dict[str, Any]:
        return {"data": list(self._contacts)}

    async def create_contact_request(
        self, recipient_handle: str, message: str | None = None
    ) -> dict[str, Any]:
        request = {
            "id": str(uuid.uuid4()),
            "recipient_handle": recipient_handle,
            "message": message,
            "status": "pending",
        }
        self.contact_requests_created.append(request)
        return request

    async def list_received_contact_requests(
        self, page: int | None = None, page_size: int | None = None
    ) -> dict[str, Any]:
        return {"data": []}

    async def list_sent_contact_requests(
        self,
        status: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return {"data": list(self.contact_requests_created)}

    async def approve_contact_request(self, request_id: str) -> dict[str, Any]:
        return self._respond_contact_request(request_id, "approved")

    async def reject_contact_request(self, request_id: str) -> dict[str, Any]:
        return self._respond_contact_request(request_id, "rejected")

    async def cancel_contact_request(self, request_id: str) -> dict[str, Any]:
        return self._respond_contact_request(request_id, "cancelled")

    def _respond_contact_request(self, request_id: str, status: str) -> dict[str, Any]:
        response = {"id": request_id, "status": status}
        self.contact_requests_responded.append(response)
        return response

    async def resolve_handle(self, handle: str) -> dict[str, Any]:
        for entity in (*self._contacts, *self._peers):
            if entity.get("handle") == handle:
                return entity
        raise RuntimeError(f"handle not found: {handle}")

    async def remove_my_contact(
        self, contact_id: str | None = None, handle: str | None = None
    ) -> dict[str, Any] | str:
        if not contact_id and not handle:
            return "Error: Either contact_id or handle must be provided"
        self._contacts = [
            c
            for c in self._contacts
            if c.get("id") != contact_id and c.get("handle") != handle
        ]
        return {"status": "removed"}

    # --- messages ---

    async def list_my_chat_messages(
        self,
        chat_id: str,
        page: int | None = None,
        page_size: int | None = None,
        message_type: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        return {"data": []}

    async def send_my_chat_message(
        self, chat_id: str, content: str, recipients: str
    ) -> dict[str, Any] | str:
        recipient_names = [
            name.strip().lower() for name in recipients.split(",") if name.strip()
        ]
        if not recipient_names:
            return "Error: recipients cannot be empty"

        participants = self._chat_participants.get(chat_id, [])
        name_to_participant = {
            p["name"].lower(): p for p in participants if p.get("name")
        }
        not_found = [
            name for name in recipient_names if name not in name_to_participant
        ]
        if not_found:
            available = list(name_to_participant.keys())
            return (
                f"Error: Not found: {', '.join(not_found)}. "
                f"Available: {', '.join(available)}"
            )

        message = {
            "id": f"msg-{len(self.messages_sent)}",
            "chat_id": chat_id,
            "content": content,
            "recipients": recipient_names,
        }
        self.messages_sent.append(message)
        return message

    # --- participants ---

    async def list_my_chat_participants(
        self, chat_id: str, participant_type: str | None = None
    ) -> dict[str, Any]:
        return {"data": list(self._chat_participants.get(chat_id, []))}

    async def add_my_chat_participant(
        self, chat_id: str, participant_id: str, role: str | None = None
    ) -> str:
        participant = {"id": participant_id, "role": role or "member"}
        self._chat_participants.setdefault(chat_id, []).append(participant)
        self.participants_added.append(participant)
        return f"Added participant: {participant_id}"

    async def remove_my_chat_participant(
        self, chat_id: str, participant_id: str
    ) -> str:
        self._chat_participants[chat_id] = [
            p
            for p in self._chat_participants.get(chat_id, [])
            if p.get("id") != participant_id
        ]
        self.participants_removed.append({"id": participant_id})
        return f"Removed participant: {participant_id}"

    # --- memories ---

    async def list_user_memories(
        self,
        chat_room_id: str | None = None,
        scope: str | None = None,
        system: str | None = None,
        memory_type: str | None = None,
        segment: str | None = None,
        content_query: str | None = None,
        page_size: int | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        page = self.memories[: page_size or len(self.memories)]
        return {"data": page}

    async def get_user_memory(self, memory_id: str) -> dict[str, Any]:
        memory = next((m for m in self.memories if m["id"] == memory_id), None)
        if memory is None:
            raise RuntimeError("Failed to get memory - no response data")
        return deepcopy(memory)

    async def supersede_user_memory(self, memory_id: str) -> dict[str, Any]:
        return self._set_memory_status(memory_id, "superseded")

    async def archive_user_memory(self, memory_id: str) -> dict[str, Any]:
        return self._set_memory_status(memory_id, "archived")

    async def restore_user_memory(self, memory_id: str) -> dict[str, Any]:
        return self._set_memory_status(memory_id, "active")

    async def delete_user_memory(self, memory_id: str) -> dict[str, Any]:
        self.memories = [m for m in self.memories if m["id"] != memory_id]
        return {"deleted": True, "id": memory_id}

    def _set_memory_status(self, memory_id: str, status: str) -> dict[str, Any]:
        for memory in self.memories:
            if memory["id"] == memory_id:
                memory["status"] = status
                return deepcopy(memory)
        raise RuntimeError("Failed to update memory - no response data")

    # --- profile / peers ---

    async def get_my_profile(self) -> dict[str, Any]:
        return dict(self._profile)

    async def update_my_profile(
        self, first_name: str | None = None, last_name: str | None = None
    ) -> dict[str, Any] | str:
        if first_name is None and last_name is None:
            return (
                "Error: At least one field (first_name or last_name) must be provided"
            )
        if first_name is not None:
            self._profile["first_name"] = first_name
        if last_name is not None:
            self._profile["last_name"] = last_name
        return dict(self._profile)

    async def list_my_peers(
        self,
        not_in_chat: str | None = None,
        peer_type: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return {"data": list(self._peers)}


async def advertised_schemas(session: ClientSession) -> dict[str, dict[str, Any]]:
    """Project a real ``list_tools()`` round trip into a snapshot-comparable dict.

    Keyed by tool name (sorted, for a deterministic diff), each entry carries
    exactly the fields a wire-contract change would touch: description and
    input schema. Used by the wire-schema snapshot test to guard the
    published band-mcp contract.
    """
    result = await session.list_tools()
    return {
        tool.name: {"description": tool.description, "inputSchema": tool.inputSchema}
        for tool in sorted(result.tools, key=lambda t: t.name)
    }
