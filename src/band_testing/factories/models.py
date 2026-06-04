"""Mock data factory for creating test objects."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from band_testing.factories.base import make_pydantic_mock
from band_testing.factories.examples import (
    AGENTME,
    CHATEVENT,
    CHATMESSAGE,
    CHATPARTICIPANT,
    CHATROOM,
    DELETED_AGENT,
    OWNED_AGENT,
    PEER,
    REGISTERED_AGENT,
    USER_PROFILE,
)


class MockDataFactory:
    """Factory for creating mock SDK response objects.

    Usage:
        from band_testing.factories import factory

        agent = factory.agent_me(id="agent-123", name="TestBot")
        response = factory.response(agent)
    """

    @staticmethod
    def agent_me(
        id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        owner_uuid: str | None = None,
    ) -> Mock:
        """Create a mock AgentMe object with OpenAPI example defaults."""
        return make_pydantic_mock(
            id=id or AGENTME["id"],
            name=name or AGENTME["name"],
            description=description or AGENTME["description"],
            owner_uuid=owner_uuid or AGENTME["owner_uuid"],
            inserted_at=AGENTME["inserted_at"],
            updated_at=AGENTME["updated_at"],
        )

    @staticmethod
    def peer(
        id: str | None = None,
        name: str | None = None,
        type: str | None = None,
        description: str | None = None,
        is_external: bool | None = None,
        is_global: bool | None = None,
    ) -> Mock:
        """Create a mock Peer object with OpenAPI example defaults."""
        return make_pydantic_mock(
            id=id or PEER["id"],
            name=name or PEER["name"],
            type=type or PEER["type"],
            description=description or PEER["description"],
            is_external=is_external if is_external is not None else PEER["is_external"],
            is_global=is_global if is_global is not None else PEER["is_global"],
        )

    @staticmethod
    def chat_room(
        id: str | None = None,
        title: str | None = None,
        task_id: str | None = None,
    ) -> Mock:
        """Create a mock ChatRoom object with OpenAPI example defaults."""
        return make_pydantic_mock(
            id=id or CHATROOM["id"],
            title=title if title is not None else CHATROOM["title"],
            task_id=task_id,
            inserted_at=CHATROOM["inserted_at"],
            updated_at=CHATROOM["updated_at"],
        )

    @staticmethod
    def chat_participant(
        id: str | None = None,
        name: str | None = None,
        type: str | None = None,
        role: str = "member",
        status: str | None = None,
        username: str | None = None,
        display_name: str | None = None,
    ) -> Mock:
        """Create a mock ChatParticipant object with OpenAPI example defaults.

        Note: username and display_name are explicitly set to prevent Mock's
        automatic attribute creation from interfering with participant lookups.
        """
        return make_pydantic_mock(
            id=id or CHATPARTICIPANT["id"],
            name=name or CHATPARTICIPANT["name"],
            type=type or CHATPARTICIPANT["type"],
            role=role,
            status=status or CHATPARTICIPANT["status"],
            username=username,
            display_name=display_name,
        )

    @staticmethod
    def chat_message(
        id: str | None = None,
        content: str | None = None,
        chat_room_id: str | None = None,
        sender_id: str | None = None,
        sender_name: str | None = None,
        sender_type: str | None = None,
        message_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Mock:
        """Create a mock ChatMessage object with OpenAPI example defaults."""
        return make_pydantic_mock(
            id=id or CHATMESSAGE["id"],
            content=content or CHATMESSAGE["content"],
            chat_room_id=chat_room_id or CHATMESSAGE["chat_room_id"],
            sender_id=sender_id or CHATMESSAGE["sender_id"],
            sender_name=sender_name or CHATMESSAGE["sender_name"],
            sender_type=sender_type or CHATMESSAGE["sender_type"],
            message_type=message_type or CHATMESSAGE["message_type"],
            metadata=metadata if metadata is not None else CHATMESSAGE["metadata"],
            inserted_at=CHATMESSAGE["inserted_at"],
            updated_at=CHATMESSAGE["updated_at"],
        )

    @staticmethod
    def chat_event(
        id: str | None = None,
        content: str | None = None,
        chat_room_id: str | None = None,
        sender_id: str | None = None,
        sender_name: str | None = None,
        sender_type: str | None = None,
        message_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Mock:
        """Create a mock ChatEvent object with OpenAPI example defaults."""
        return make_pydantic_mock(
            id=id or CHATEVENT["id"],
            content=content or CHATEVENT["content"],
            chat_room_id=chat_room_id or CHATEVENT["chat_room_id"],
            sender_id=sender_id or CHATEVENT["sender_id"],
            sender_name=sender_name or CHATEVENT["sender_name"],
            sender_type=sender_type or CHATEVENT["sender_type"],
            message_type=message_type or "thought",
            metadata=metadata if metadata is not None else CHATEVENT["metadata"],
            inserted_at=CHATEVENT["inserted_at"],
            updated_at=CHATEVENT["updated_at"],
        )

    @staticmethod
    def response(data: Any, meta: dict[str, Any] | None = None) -> Mock:
        """Create a mock API response wrapper.

        Creates a response that can be serialized by serialize_response().
        The data field can be a mock object with model_dump() or None.
        """

        def model_dump(**kwargs: object) -> dict[str, Any]:
            if data is None:
                return {"data": None, "meta": meta}
            if hasattr(data, "model_dump"):
                return {"data": data.model_dump(**kwargs), "meta": meta}
            return {"data": data, "meta": meta}

        response = Mock()
        response.data = data
        response.meta = meta
        response.model_dump = model_dump
        return response

    @staticmethod
    def list_response(items: list[Any], meta: dict[str, Any] | None = None) -> Mock:
        """Create a mock API response for list endpoints.

        Creates a list response that can be serialized by serialize_response().
        """
        meta = meta or {"page": 1, "page_size": 50, "total": len(items)}

        def model_dump(**kwargs: object) -> dict[str, Any]:
            data_items = []
            for item in items:
                if hasattr(item, "model_dump"):
                    data_items.append(item.model_dump(**kwargs))
                else:
                    data_items.append(item)
            return {"data": data_items, "meta": meta}

        response = Mock()
        response.data = items
        response.meta = meta
        response.model_dump = model_dump
        return response

    # =========================================================================
    # User API (human_api) Factory Methods
    # =========================================================================

    @staticmethod
    def user_profile(
        id: str | None = None,
        name: str | None = None,
        email: str | None = None,
    ) -> Mock:
        """Create a mock UserProfile object with OpenAPI example defaults."""
        return make_pydantic_mock(
            id=id or USER_PROFILE["id"],
            name=name or USER_PROFILE["name"],
            email=email or USER_PROFILE["email"],
            inserted_at=USER_PROFILE["inserted_at"],
            updated_at=USER_PROFILE["updated_at"],
        )

    @staticmethod
    def owned_agent(
        id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        owner_id: str | None = None,
        is_external: bool = True,
    ) -> Mock:
        """Create a mock OwnedAgent object for list_my_agents response."""
        return make_pydantic_mock(
            id=id or OWNED_AGENT["id"],
            name=name or OWNED_AGENT["name"],
            description=description or OWNED_AGENT["description"],
            owner_id=owner_id or OWNED_AGENT["owner_id"],
            is_external=is_external,
            inserted_at=OWNED_AGENT["inserted_at"],
            updated_at=OWNED_AGENT["updated_at"],
        )

    @staticmethod
    def registered_agent(
        agent_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        api_key: str | None = None,
    ) -> Mock:
        """Create a mock RegisteredAgent response (includes agent and credentials).

        This is the response from register_my_agent - the API key is returned
        only once and should be stored securely.
        """
        agent = make_pydantic_mock(
            id=agent_id or REGISTERED_AGENT["agent"]["id"],
            name=name or REGISTERED_AGENT["agent"]["name"],
            description=description or REGISTERED_AGENT["agent"]["description"],
            owner_id=REGISTERED_AGENT["agent"]["owner_id"],
            is_external=REGISTERED_AGENT["agent"]["is_external"],
            inserted_at=REGISTERED_AGENT["agent"]["inserted_at"],
            updated_at=REGISTERED_AGENT["agent"]["updated_at"],
        )
        credentials = make_pydantic_mock(
            api_key=api_key or REGISTERED_AGENT["credentials"]["api_key"],
        )
        return make_pydantic_mock(
            agent=agent,
            credentials=credentials,
        )

    @staticmethod
    def deleted_agent(
        id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        executions_deleted: int = 0,
    ) -> Mock:
        """Create a mock DeletedAgent response from delete_my_agent."""
        return make_pydantic_mock(
            id=id or DELETED_AGENT["id"],
            name=name or DELETED_AGENT["name"],
            description=description or DELETED_AGENT["description"],
            executions_deleted=executions_deleted,
        )


# Singleton factory instance for convenience
factory = MockDataFactory()
