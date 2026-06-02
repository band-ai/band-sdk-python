"""OpenAPI examples extracted from the spec.

These are default values used by the mock data factory.
Based on the Band API OpenAPI specification.
"""

from __future__ import annotations

# fmt: off
# ruff: noqa: E501

# AgentMe - Current agent's profile
AGENTME = {
    "description": "Provides weather updates and forecasts using external APIs",
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "inserted_at": "2025-01-15T10:30:00Z",
    "name": "Weather Assistant",
    "owner_uuid": "7fa85f64-5717-4562-b3fc-2c963f66afa6",
    "updated_at": "2025-01-15T14:45:00Z",
}

# Peer - An entity available for interaction in chat rooms (user or agent)
PEER = {
    "description": "Analyzes datasets and generates reports",
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "is_external": False,
    "is_global": True,
    "name": "Data Analyst",
    "type": "Agent",
}

# ChatRoom - A chat room
CHATROOM = {
    "id": "daca00d0-eb6b-4db1-8201-c46015c93d04",
    "inserted_at": "2025-01-15T10:30:00Z",
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Q4 Sales Analysis Discussion",
    "updated_at": "2025-01-15T14:45:00Z",
}

# ChatParticipant - A chat room participant
CHATPARTICIPANT = {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "name": "Data Analyst",
    "status": "active",
    "type": "Agent",
}

# ChatMessage - A chat message
CHATMESSAGE = {
    "chat_room_id": "daca00d0-eb6b-4db1-8201-c46015c93d04",
    "content": "@DataAnalyst please analyze the Q4 sales data",
    "id": "a1b2c3d4-e5f6-4a5b-9c8d-e7f8a9b0c1d2",
    "inserted_at": "2025-01-15T10:30:00Z",
    "message_type": "text",
    "metadata": {"mentions": [{"id": "uuid", "username": "DataAnalyst"}]},
    "sender_id": "550e8400-e29b-41d4-a716-446655440000",
    "sender_name": "John Smith",
    "sender_type": "User",
    "updated_at": "2025-01-15T10:30:00Z",
}

# ChatEvent - A chat event (tool_call, tool_result, thought, etc.)
CHATEVENT = {
    "chat_room_id": "daca00d0-eb6b-4db1-8201-c46015c93d04",
    "content": "Calling send_direct_message_service",
    "id": "e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b",
    "inserted_at": "2025-01-15T10:30:00Z",
    "metadata": {
        "function": {
            "arguments": {"message": "Hello!", "recipients": []},
            "name": "send_direct_message_service",
        },
        "id": "chatcmpl-tool-abc123",
        "type": "function",
    },
    "sender_id": "550e8400-e29b-41d4-a716-446655440000",
    "sender_name": "Weather Assistant",
    "sender_type": "Agent",
    "updated_at": "2025-01-15T10:30:00Z",
}

# ChatEventMessageType enum values
CHATEVENTMESSAGETYPE_VALUES = ["tool_call", "tool_result", "thought", "error", "task"]
CHATEVENTMESSAGETYPE_EXAMPLE = "tool_call"

# ParticipantRole enum values
PARTICIPANTROLE_VALUES = ["owner", "admin", "member"]
PARTICIPANTROLE_EXAMPLE = "member"

# ============================================================================
# User API (human_api) Examples
# ============================================================================

# UserProfile - User's profile information
USER_PROFILE = {
    "id": "7fa85f64-5717-4562-b3fc-2c963f66afa6",
    "name": "Test User",
    "email": "test@example.com",
    "inserted_at": "2025-01-15T10:30:00Z",
    "updated_at": "2025-01-15T14:45:00Z",
}

# OwnedAgent - Agent owned by a user (for list_my_agents)
OWNED_AGENT = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "Weather Assistant",
    "description": "Provides weather updates and forecasts using external APIs",
    "owner_id": "7fa85f64-5717-4562-b3fc-2c963f66afa6",
    "is_external": True,
    "inserted_at": "2025-01-15T10:30:00Z",
    "updated_at": "2025-01-15T14:45:00Z",
}

# RegisteredAgent - Response from register_my_agent (includes credentials)
REGISTERED_AGENT = {
    "agent": {
        "id": "660e8400-e29b-41d4-a716-446655440001",
        "name": "SDK Test Agent",
        "description": "Agent created by SDK integration tests",
        "owner_id": "7fa85f64-5717-4562-b3fc-2c963f66afa6",
        "is_external": True,
        "inserted_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T10:30:00Z",
    },
    "credentials": {
        "api_key": "band_1234567890_TestApiKeyForUnitTests",
    },
}

# DeletedAgent - Response from delete_my_agent
DELETED_AGENT = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "Weather Assistant",
    "description": "Provides weather updates and forecasts using external APIs",
    "executions_deleted": 0,
}

# Combined examples dict for easy access
EXAMPLES = {
    "AgentMe": AGENTME,
    "Peer": PEER,
    "ChatRoom": CHATROOM,
    "ChatParticipant": CHATPARTICIPANT,
    "ChatMessage": CHATMESSAGE,
    "ChatEvent": CHATEVENT,
    "UserProfile": USER_PROFILE,
    "OwnedAgent": OWNED_AGENT,
    "RegisteredAgent": REGISTERED_AGENT,
    "DeletedAgent": DELETED_AGENT,
}

# fmt: on
