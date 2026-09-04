"""Registration metadata for a built-in Band tool: which surface it's on,
its input model, and the ``AgentTools``/``HumanTools`` method it dispatches to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel


class Surface(StrEnum):
    """The two surfaces a built-in Band tool can be registered on."""

    AGENT = "agent"
    HUMAN = "human"


class BandTool(StrEnum):
    """The agent-surface Band tool names.

    One vocabulary for every site that checks a tool's identity by name:
    room-posting detection, room-binding classification, capability gating,
    mention-hint enrichment, and the adapters' own per-tool branches (the
    send-message special cases in crewai/claude_sdk/agno/letta, the
    image-vision passthrough only a ``READ_ROOM_FILE`` result can trigger).
    Members are ``str``, so they compare, hash, and serialize as the wire
    name. The human surface is a separate vocabulary, deliberately not
    modeled here.
    """

    SEND_MESSAGE = "band_send_message"
    SEND_EVENT = "band_send_event"
    ADD_PARTICIPANT = "band_add_participant"
    REMOVE_PARTICIPANT = "band_remove_participant"
    GET_PARTICIPANTS = "band_get_participants"
    LOOKUP_PEERS = "band_lookup_peers"
    CREATE_CHATROOM = "band_create_chatroom"
    LIST_CONTACTS = "band_list_contacts"
    ADD_CONTACT = "band_add_contact"
    REMOVE_CONTACT = "band_remove_contact"
    LIST_CONTACT_REQUESTS = "band_list_contact_requests"
    RESPOND_CONTACT_REQUEST = "band_respond_contact_request"
    LIST_MEMORIES = "band_list_memories"
    STORE_MEMORY = "band_store_memory"
    GET_MEMORY = "band_get_memory"
    SUPERSEDE_MEMORY = "band_supersede_memory"
    ARCHIVE_MEMORY = "band_archive_memory"
    LIST_ROOM_FILES = "band_list_room_files"
    READ_ROOM_FILE = "band_read_room_file"
    SEND_ROOM_FILE = "band_send_room_file"
    LIST_TASKS = "band_list_tasks"
    CREATE_TASK = "band_create_task"
    GET_TASK = "band_get_task"
    UPDATE_TASK = "band_update_task"
    GET_TASK_HISTORY = "band_get_task_history"
    GET_BOARD = "band_get_board"
    SET_BOARD = "band_set_board"


class ToolCategory(StrEnum):
    """The ``AdapterFeatures.include_categories``/``exclude_categories``
    buckets a built-in Band tool can fall into.

    Single source of truth for these five values -- every per-adapter
    category mapping (e.g. ``_TOOL_CATEGORIES``) builds its dict from this
    enum instead of retyping the strings.
    """

    CHAT = "chat"
    CONTACTS = "contacts"
    MEMORY = "memory"
    FILES = "files"
    TASKS = "tasks"


@dataclass(frozen=True)
class ToolDefinition:
    """Metadata for a built-in Band tool."""

    name: str
    input_model: type[BaseModel]
    method_name: str
    surface: Surface = Surface.AGENT
