"""The tool-name registry: every built-in ``ToolDefinition``, the name-sets
that group them (memory/contacts/files/tasks/read-only/event/human-surface),
capability gating, and MCP tool-name helpers.

Single source of truth for "what tools exist and which capability gates
each one" -- ``AgentTools``/``HumanTools`` dispatch against this registry
rather than hardcoding their own tool lists.
"""

from __future__ import annotations

import base64
from collections.abc import Collection
from typing import Any

from pydantic import BaseModel

from band.core.types import Capability
from band.runtime.tools.inputs import (
    AddContactInput,
    AddMyChatParticipantInput,
    AddParticipantInput,
    ApproveContactRequestInput,
    ArchiveMemoryInput,
    ArchiveUserMemoryInput,
    CancelContactRequestInput,
    CreateChatroomInput,
    CreateContactRequestInput,
    CreateMyChatRoomInput,
    CreateTaskInput,
    DeleteUserMemoryInput,
    GetBoardInput,
    GetMemoryInput,
    GetMyChatRoomInput,
    GetMyProfileInput,
    GetParticipantsInput,
    GetTaskHistoryInput,
    GetTaskInput,
    GetUserMemoryInput,
    ListContactRequestsInput,
    ListContactsInput,
    ListMemoriesInput,
    ListMyAgentsInput,
    ListMyChatMessagesInput,
    ListMyChatParticipantsInput,
    ListMyChatsInput,
    ListMyContactsInput,
    ListMyPeersInput,
    ListReceivedContactRequestsInput,
    ListRoomFilesInput,
    ListSentContactRequestsInput,
    ListTasksInput,
    ListUserMemoriesInput,
    LookupPeersInput,
    ReadRoomFileInput,
    RegisterMyAgentInput,
    RejectContactRequestInput,
    RemoveContactInput,
    RemoveMyChatParticipantInput,
    RemoveMyContactInput,
    RemoveParticipantInput,
    ResolveHandleInput,
    RespondContactRequestInput,
    RestoreUserMemoryInput,
    SendEventInput,
    SendMessageInput,
    SendMyChatMessageInput,
    SendRoomFileInput,
    SetBoardInput,
    StoreMemoryInput,
    SupersedeMemoryInput,
    SupersedeUserMemoryInput,
    UpdateMyProfileInput,
    UpdateTaskInput,
)
from band.runtime.tools.types import BandTool, Surface, ToolCategory, ToolDefinition

# The name the Band MCP server registers under. MCP clients key tool
# namespacing off it (e.g. Copilot's hyphen-joined ``band-<tool>``), and
# adapters reference it when advertising the server in a session config.
# The one source of truth: ``_resolve_mcp_tool_name`` anchors its prefix
# match here, and ``integrations.mcp.backends`` names the server from it.
BAND_MCP_SERVER_NAME = "band"

# Tool names whose successful call posts a visible message into the room.
# Bridge adapters (copilot_sdk, codex, ACP client) use this to suppress their
# fallback text relay once the turn has already replied in the room, so the
# reply is delivered exactly once. band-mcp 1.3.2+ advertises the SDK-native
# ``band_send_message`` (its registrar reuses these SDK tool definitions), which
# the ``<server>-`` prefix match already covers; ``create_agent_chat_message``
# is the legacy band-mcp <=1.3.1 spelling, kept so older out-of-process servers
# still match. ``band_send_room_file`` also posts a message (the file's
# attaching message), same reply-once reasoning.
ROOM_POSTING_TOOL_NAMES: frozenset[str] = frozenset(
    {BandTool.SEND_MESSAGE, "create_agent_chat_message", BandTool.SEND_ROOM_FILE}
)


def _resolve_mcp_tool_name(tool_name: str, names: Collection[str]) -> str | None:
    """The member of ``names`` behind ``tool_name``'s MCP spelling, if any.

    The one resolver for the one MCP naming convention seen in practice: the
    Band loopback server's own hyphen-joined ``band-<tool>`` prefix (e.g.
    Copilot CLI surfaces ``band_send_message`` as ``band-band_send_message``;
    band-mcp <=1.3.1's legacy spelling arrives as
    ``band-create_agent_chat_message``). Anchored to ``BAND_MCP_SERVER_NAME``
    specifically -- not any prefix before a hyphen -- so an unrelated MCP
    server's own tool (e.g. ``other-band_send_message``) never resolves as a
    Band tool. Other spellings (``mcp__server__tool``, ``server.tool``) are
    not matched either -- no wired backend uses them. Extend here when such a
    backend is added.
    """
    if tool_name in names:
        return tool_name
    prefix = f"{BAND_MCP_SERVER_NAME}-"
    suffix = tool_name.removeprefix(prefix)
    return suffix if suffix != tool_name and suffix in names else None


def is_room_posting_tool(tool_name: str) -> bool:
    """True when a successful call of ``tool_name`` posts a message to the room.

    Tolerates the Band MCP server's own ``band-`` spelling (see
    ``_resolve_mcp_tool_name``) but nothing else -- an unrelated MCP server's
    tool that merely ends in ``-band_send_message`` (e.g.
    ``other-band_send_message``) never resolves as room-posting, since its
    prefix isn't ``band``. A miss only costs a duplicate reply (the
    pre-suppression behavior), never a wrong post.
    """
    return _resolve_mcp_tool_name(tool_name, ROOM_POSTING_TOOL_NAMES) is not None


def canonicalize_mcp_tool_name(tool_name: str, own_names: Collection[str]) -> str:
    """The canonical band tool name behind the Band MCP server's ``band-`` spelling.

    Narrated ``tool_call``/``tool_result`` events must carry the canonical
    name like every other adapter's, so consumers match on one vocabulary.
    A name that doesn't reveal one of ``own_names`` behind ``band-`` passes
    through untouched -- including another MCP server's own tool.
    """
    return _resolve_mcp_tool_name(tool_name, own_names) or tool_name


# The agent tools whose MCP handler takes a room id (``chat_id`` on the wire)
# as a kwarg -- i.e. the handler is room-scoped. Related to but distinct from
# ROOM_POSTING_TOOL_NAMES above (that set is about which *successful calls*
# post a room message; this one is about which tools need a room id at all).
#
# AgentTools is constructor-scoped (``AgentTools(room_id=..., rest=...)``), so
# these method signatures don't carry a room field themselves -- an MCP front
# door has to re-add it at the transport layer. This is the published band-mcp
# 1.3.2 contract (canonical field name ``chat_id``); the CLI front door
# (packages/band-mcp) classifies per-tool against this set, while the embedded
# front door (src/band/integrations/mcp/local_server.py) wraps every agent
# tool uniformly instead, since chat_id is its routing key for AgentTools
# instance selection.
AGENT_ROOM_BOUND_TOOL_NAMES: frozenset[str] = frozenset(
    {
        BandTool.SEND_MESSAGE,
        BandTool.SEND_EVENT,
        BandTool.ADD_PARTICIPANT,
        BandTool.REMOVE_PARTICIPANT,
        BandTool.GET_PARTICIPANTS,
        BandTool.LOOKUP_PEERS,
        BandTool.LIST_ROOM_FILES,
        BandTool.READ_ROOM_FILE,
        BandTool.SEND_ROOM_FILE,
        BandTool.LIST_TASKS,
        BandTool.CREATE_TASK,
        BandTool.GET_TASK,
        BandTool.UPDATE_TASK,
        BandTool.GET_TASK_HISTORY,
        BandTool.GET_BOARD,
        BandTool.SET_BOARD,
    }
)

# The model-facing room-identifier argument name every MCP front door and
# adapter prompt advertises -- the published band-mcp 1.3.2 wire contract's
# canonical field name. The Python-side variable is still `room_id`
# everywhere; only text the model sees (schemas, prompts) uses this. Single
# source of truth so a producer (schema field name) and its consumers
# (per-turn prompt text in opencode/letta/acp/claude_sdk) can't drift apart.
CHAT_ID_FIELD_NAME = "chat_id"

# The chat_id field's max length wherever an MCP front door adds or pins it
# (engine.py's extend_with_chat_id/pin_existing_chat_id) -- kept next to the
# field's canonical name above rather than split across files, since both
# describe the same field.
CHAT_ID_MAX_LENGTH = 255


def classify_room_binding(definition: ToolDefinition) -> tuple[bool, bool]:
    """Return ``(is_agent_room_bound, is_human_room_bound)`` for a definition.

    Agent tools are classified against the hard-coded
    ``AGENT_ROOM_BOUND_TOOL_NAMES`` set (their SDK input models carry no room
    field to inspect -- see that set's docstring). Human tools are classified
    by inspecting ``input_model.model_fields`` for ``chat_id``: ``HumanTools``
    is not constructor-scoped, so its room-bound methods already carry
    ``chat_id`` as a normal parameter, and that model field is the source of
    truth.

    This is the CLI front door's classifier (the published band-mcp 1.3.2
    contract). The embedded front door does not call this for agent tools --
    it wraps every agent tool uniformly instead (divergence-matrix row 2).
    """
    match definition.surface:
        case Surface.AGENT:
            return (definition.name in AGENT_ROOM_BOUND_TOOL_NAMES, False)
        case Surface.HUMAN:
            return (False, CHAT_ID_FIELD_NAME in definition.input_model.model_fields)
        case _:
            return (False, False)


# Registry mapping tool names to their schemas and bound AgentTools methods.
# Single source of truth for each tool's name: typed once, as the
# ToolDefinition's own `name=` field. TOOL_DEFINITIONS below derives its
# keys from that instead of retyping the name a second time as a dict key.
_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name=BandTool.SEND_MESSAGE,
        input_model=SendMessageInput,
        method_name="send_message",
    ),
    ToolDefinition(
        name=BandTool.SEND_EVENT,
        input_model=SendEventInput,
        method_name="send_event",
    ),
    ToolDefinition(
        name=BandTool.ADD_PARTICIPANT,
        input_model=AddParticipantInput,
        method_name="add_participant",
    ),
    ToolDefinition(
        name=BandTool.REMOVE_PARTICIPANT,
        input_model=RemoveParticipantInput,
        method_name="remove_participant",
    ),
    ToolDefinition(
        name=BandTool.LOOKUP_PEERS,
        input_model=LookupPeersInput,
        method_name="lookup_peers",
    ),
    ToolDefinition(
        name=BandTool.GET_PARTICIPANTS,
        input_model=GetParticipantsInput,
        method_name="get_participants",
    ),
    ToolDefinition(
        name=BandTool.CREATE_CHATROOM,
        input_model=CreateChatroomInput,
        method_name="create_chatroom",
    ),
    ToolDefinition(
        name=BandTool.LIST_CONTACTS,
        input_model=ListContactsInput,
        method_name="list_contacts",
    ),
    ToolDefinition(
        name=BandTool.ADD_CONTACT,
        input_model=AddContactInput,
        method_name="add_contact",
    ),
    ToolDefinition(
        name=BandTool.REMOVE_CONTACT,
        input_model=RemoveContactInput,
        method_name="remove_contact",
    ),
    ToolDefinition(
        name=BandTool.LIST_CONTACT_REQUESTS,
        input_model=ListContactRequestsInput,
        method_name="list_contact_requests",
    ),
    ToolDefinition(
        name=BandTool.RESPOND_CONTACT_REQUEST,
        input_model=RespondContactRequestInput,
        method_name="respond_contact_request",
    ),
    ToolDefinition(
        name=BandTool.LIST_MEMORIES,
        input_model=ListMemoriesInput,
        method_name="list_memories",
    ),
    ToolDefinition(
        name=BandTool.STORE_MEMORY,
        input_model=StoreMemoryInput,
        method_name="store_memory",
    ),
    ToolDefinition(
        name=BandTool.GET_MEMORY,
        input_model=GetMemoryInput,
        method_name="get_memory",
    ),
    ToolDefinition(
        name=BandTool.SUPERSEDE_MEMORY,
        input_model=SupersedeMemoryInput,
        method_name="supersede_memory",
    ),
    ToolDefinition(
        name=BandTool.ARCHIVE_MEMORY,
        input_model=ArchiveMemoryInput,
        method_name="archive_memory",
    ),
    ToolDefinition(
        name=BandTool.LIST_ROOM_FILES,
        input_model=ListRoomFilesInput,
        method_name="list_room_files",
    ),
    ToolDefinition(
        name=BandTool.READ_ROOM_FILE,
        input_model=ReadRoomFileInput,
        method_name="read_room_file",
    ),
    ToolDefinition(
        name=BandTool.SEND_ROOM_FILE,
        input_model=SendRoomFileInput,
        method_name="send_room_file",
    ),
    ToolDefinition(
        name=BandTool.LIST_TASKS,
        input_model=ListTasksInput,
        method_name="list_tasks",
    ),
    ToolDefinition(
        name=BandTool.CREATE_TASK,
        input_model=CreateTaskInput,
        method_name="create_task",
    ),
    ToolDefinition(
        name=BandTool.GET_TASK,
        input_model=GetTaskInput,
        method_name="get_task",
    ),
    ToolDefinition(
        name=BandTool.UPDATE_TASK,
        input_model=UpdateTaskInput,
        method_name="update_task",
    ),
    ToolDefinition(
        name=BandTool.GET_TASK_HISTORY,
        input_model=GetTaskHistoryInput,
        method_name="get_task_history",
    ),
    ToolDefinition(
        name=BandTool.GET_BOARD,
        input_model=GetBoardInput,
        method_name="get_board",
    ),
    ToolDefinition(
        name=BandTool.SET_BOARD,
        input_model=SetBoardInput,
        method_name="set_board",
    ),
    # --- Human tools (surface="human") ---
    # One entry per method in the Phase 1 human-tool mapping table.
    # Method names match HumanTools attributes; hasattr(HumanTools, method_name)
    # must resolve for every surface="human" definition.
    ToolDefinition(
        name="band_list_my_agents",
        input_model=ListMyAgentsInput,
        method_name="list_my_agents",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_register_my_agent",
        input_model=RegisterMyAgentInput,
        method_name="register_my_agent",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_list_my_chats",
        input_model=ListMyChatsInput,
        method_name="list_my_chats",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_create_my_chat_room",
        input_model=CreateMyChatRoomInput,
        method_name="create_my_chat_room",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_get_my_chat_room",
        input_model=GetMyChatRoomInput,
        method_name="get_my_chat_room",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_list_my_contacts",
        input_model=ListMyContactsInput,
        method_name="list_my_contacts",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_create_contact_request",
        input_model=CreateContactRequestInput,
        method_name="create_contact_request",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_list_received_contact_requests",
        input_model=ListReceivedContactRequestsInput,
        method_name="list_received_contact_requests",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_list_sent_contact_requests",
        input_model=ListSentContactRequestsInput,
        method_name="list_sent_contact_requests",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_approve_contact_request",
        input_model=ApproveContactRequestInput,
        method_name="approve_contact_request",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_reject_contact_request",
        input_model=RejectContactRequestInput,
        method_name="reject_contact_request",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_cancel_contact_request",
        input_model=CancelContactRequestInput,
        method_name="cancel_contact_request",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_resolve_handle",
        input_model=ResolveHandleInput,
        method_name="resolve_handle",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_remove_my_contact",
        input_model=RemoveMyContactInput,
        method_name="remove_my_contact",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_list_my_chat_messages",
        input_model=ListMyChatMessagesInput,
        method_name="list_my_chat_messages",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_send_my_chat_message",
        input_model=SendMyChatMessageInput,
        method_name="send_my_chat_message",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_list_my_chat_participants",
        input_model=ListMyChatParticipantsInput,
        method_name="list_my_chat_participants",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_add_my_chat_participant",
        input_model=AddMyChatParticipantInput,
        method_name="add_my_chat_participant",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_remove_my_chat_participant",
        input_model=RemoveMyChatParticipantInput,
        method_name="remove_my_chat_participant",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_list_user_memories",
        input_model=ListUserMemoriesInput,
        method_name="list_user_memories",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_get_user_memory",
        input_model=GetUserMemoryInput,
        method_name="get_user_memory",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_supersede_user_memory",
        input_model=SupersedeUserMemoryInput,
        method_name="supersede_user_memory",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_archive_user_memory",
        input_model=ArchiveUserMemoryInput,
        method_name="archive_user_memory",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_restore_user_memory",
        input_model=RestoreUserMemoryInput,
        method_name="restore_user_memory",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_delete_user_memory",
        input_model=DeleteUserMemoryInput,
        method_name="delete_user_memory",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_get_my_profile",
        input_model=GetMyProfileInput,
        method_name="get_my_profile",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_update_my_profile",
        input_model=UpdateMyProfileInput,
        method_name="update_my_profile",
        surface=Surface.HUMAN,
    ),
    ToolDefinition(
        name="band_list_my_peers",
        input_model=ListMyPeersInput,
        method_name="list_my_peers",
        surface=Surface.HUMAN,
    ),
)

TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    definition.name: definition for definition in _TOOL_DEFINITIONS
}

TOOL_MODELS: dict[str, type[BaseModel]] = {
    name: definition.input_model
    for name, definition in TOOL_DEFINITIONS.items()
    if definition.surface == Surface.AGENT
}

# BandTool is the agent surface's vocabulary, so it must stay exactly the set
# of agent-surface definitions -- a new agent tool that skips the enum, or an
# enum member with no definition behind it, is a bug either way.
_AGENT_DEFINITION_NAMES: frozenset[str] = frozenset(
    definition.name
    for definition in _TOOL_DEFINITIONS
    if definition.surface == Surface.AGENT
)
if frozenset(BandTool) != _AGENT_DEFINITION_NAMES:
    raise ValueError(
        "BandTool drifted from the agent-surface tool definitions: "
        f"{frozenset(BandTool) ^ _AGENT_DEFINITION_NAMES}"
    )

# Memory tools - optional, only available for enterprise customers.
# Explicitly listed (not derived by heuristic) because memory is an opt-in
# enterprise feature and accidental inclusion of a non-memory tool would
# expose functionality that should be gated.
MEMORY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        BandTool.LIST_MEMORIES,
        BandTool.STORE_MEMORY,
        BandTool.GET_MEMORY,
        BandTool.SUPERSEDE_MEMORY,
        BandTool.ARCHIVE_MEMORY,
    }
)

# Contact tools - explicitly listed (not derived by heuristic) because a
# future tool whose name happens to contain "contact" (e.g.
# band_get_contact_context) would be silently misclassified.
CONTACT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        BandTool.LIST_CONTACTS,
        BandTool.ADD_CONTACT,
        BandTool.REMOVE_CONTACT,
        BandTool.LIST_CONTACT_REQUESTS,
        BandTool.RESPOND_CONTACT_REQUEST,
    }
)

# File tools - gated behind Capability.FILES, itself negotiated against the
# platform's `ff_file_transfer` deployment flag (see runtime/capabilities.py).
# Explicitly listed for the same reason as MEMORY_TOOL_NAMES/CONTACT_TOOL_NAMES
# above.
FILE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        BandTool.LIST_ROOM_FILES,
        BandTool.READ_ROOM_FILE,
        BandTool.SEND_ROOM_FILE,
    }
)

# Task-board tools - gated behind Capability.TASKS. Explicitly listed for the
# same reason as MEMORY_TOOL_NAMES/CONTACT_TOOL_NAMES/FILE_TOOL_NAMES above.
TASK_TOOL_NAMES: frozenset[str] = frozenset(
    {
        BandTool.LIST_TASKS,
        BandTool.CREATE_TASK,
        BandTool.GET_TASK,
        BandTool.UPDATE_TASK,
        BandTool.GET_TASK_HISTORY,
        BandTool.GET_BOARD,
        BandTool.SET_BOARD,
    }
)


# Read-only / informational agent tools - explicitly listed (not derived by a
# name heuristic) because misclassifying a write tool as read-only would weaken
# the benign-empty-answer suppression in the crewai/pydantic-ai adapters. These
# tools only *fetch* state; running one is not a terminal action and does not
# constitute a reply, so a turn that runs only these and then yields an empty
# final answer is a genuine no-response failure, not benign noise.
READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        BandTool.GET_PARTICIPANTS,
        BandTool.LOOKUP_PEERS,
        BandTool.LIST_CONTACTS,
        BandTool.LIST_CONTACT_REQUESTS,
        BandTool.LIST_MEMORIES,
        BandTool.GET_MEMORY,
        BandTool.LIST_ROOM_FILES,
        BandTool.READ_ROOM_FILE,
        BandTool.LIST_TASKS,
        BandTool.GET_TASK,
        BandTool.GET_TASK_HISTORY,
        BandTool.GET_BOARD,
    }
)

# Event-emitting tools are observational, not terminal work: band_send_event posts a
# thought/error/task event (narration/status) — not a chat reply or a durable requested
# action. Like read-only tools, a turn that only sends an event and then yields an empty
# final answer is a genuine no-response failure, not benign (see is_terminal_success).
EVENT_TOOL_NAMES: frozenset[str] = frozenset({BandTool.SEND_EVENT})

# Human-surface memory tools - parallel to MEMORY_TOOL_NAMES but on the
# ``surface="human"`` side of the registry. Used by iter_tool_definitions()
# to apply the ``Capability.MEMORY`` filter uniformly across both surfaces.
HUMAN_SURFACE_MEMORY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "band_list_user_memories",
        "band_get_user_memory",
        "band_supersede_user_memory",
        "band_archive_user_memory",
        "band_restore_user_memory",
        "band_delete_user_memory",
    }
)

# Human-surface contact tools - parallel to CONTACT_TOOL_NAMES.
HUMAN_SURFACE_CONTACT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "band_list_my_contacts",
        "band_create_contact_request",
        "band_list_received_contact_requests",
        "band_list_sent_contact_requests",
        "band_approve_contact_request",
        "band_reject_contact_request",
        "band_cancel_contact_request",
        "band_resolve_handle",
        "band_remove_my_contact",
    }
)

# Derived from TOOL_MODELS — single source of truth
ALL_TOOL_NAMES: frozenset[str] = frozenset(TOOL_MODELS.keys())


def band_tool_errored(tool_name: str | None, content: Any) -> bool:
    """Whether a Band tool call failed, by its wrapper's error convention.

    Band tool wrappers catch exceptions and return a string starting with
    ``"Error "``. Only known Band tools follow this convention (custom tools do not),
    so it is checked for ``ALL_TOOL_NAMES`` members only. (crewai detects failure
    differently — via its JSON ``status`` envelope — and does not use this helper.)
    """
    return (
        tool_name in ALL_TOOL_NAMES
        and isinstance(content, str)
        and content.startswith("Error ")
    )


def is_terminal_success(
    tool_name: str | None,
    *,
    succeeded: bool,
    custom_terminal: bool = False,
) -> bool:
    """Whether a finished tool call counts as terminal productive work.

    Single source of truth shared by the crewai / pydantic-ai adapters to decide
    whether an empty final model response is *benign* (the agent already did its
    work this turn) or a genuine no-response failure. Terminal work is:

    * a Band tool that is not read-only, not observational, and did not fail, or
    * a custom tool the caller declares terminal (``custom_terminal=True``).

    Read-only Band tools (``READ_ONLY_TOOL_NAMES``) never count — fetching state is
    not a terminal action. Observational tools (``EVENT_TOOL_NAMES`` — band_send_event
    posts a thought/error/task event) don't count either: emitting narration/status is
    not a chat reply or a durable requested action. Custom tools are **not** terminal
    by default: the SDK cannot know whether a bare custom tool is a lookup or a
    side-effecting action, so it fails loud — an empty final after only an undeclared
    custom tool surfaces as a no-response error rather than being silently swallowed.
    A custom tool that genuinely completes the turn opts in (see
    ``runtime.custom_tools.is_marked_terminal``).
    """
    if not succeeded:
        return False
    if tool_name in READ_ONLY_TOOL_NAMES or tool_name in EVENT_TOOL_NAMES:
        return False
    if tool_name in ALL_TOOL_NAMES:
        return True
    return custom_terminal


def missing_reply_error(framework: str, *, detail: str = "") -> str:
    """The room-visible error for a turn that ended without a reply going out.

    Raised by every adapter that answers through tools, so the wording lives
    once. Both endings are named because they look identical from the room and
    are told apart only by the model's last response: a plain-text final answer
    the adapter cannot post, or no output at all (empty or thinking-only), which
    is what a model that considers the exchange finished actually returns.
    """
    reasons = (
        f"{framework} finished a turn without calling band_send_message, so "
        "nothing reached the room. The model either answered in plain text "
        "instead of using the tool, or returned no output at all."
    )
    return f"{reasons} {detail}" if detail else reasons


# Fail fast on typos — catch at import time, not in a test run.
# Use explicit checks instead of ``assert`` so they are not stripped by -O.
if MEMORY_TOOL_NAMES - ALL_TOOL_NAMES:
    raise ValueError(f"Unknown memory tools: {MEMORY_TOOL_NAMES - ALL_TOOL_NAMES}")
if CONTACT_TOOL_NAMES - ALL_TOOL_NAMES:
    raise ValueError(f"Unknown contact tools: {CONTACT_TOOL_NAMES - ALL_TOOL_NAMES}")
if READ_ONLY_TOOL_NAMES - ALL_TOOL_NAMES:
    raise ValueError(
        f"Unknown read-only tools: {READ_ONLY_TOOL_NAMES - ALL_TOOL_NAMES}"
    )
if FILE_TOOL_NAMES - ALL_TOOL_NAMES:
    raise ValueError(f"Unknown file tools: {FILE_TOOL_NAMES - ALL_TOOL_NAMES}")
if TASK_TOOL_NAMES - ALL_TOOL_NAMES:
    raise ValueError(f"Unknown task tools: {TASK_TOOL_NAMES - ALL_TOOL_NAMES}")
if EVENT_TOOL_NAMES - ALL_TOOL_NAMES:
    raise ValueError(f"Unknown event tools: {EVENT_TOOL_NAMES - ALL_TOOL_NAMES}")

# Human-surface registry membership is validated against TOOL_DEFINITIONS
# (not TOOL_MODELS, which stays agent-only for back-compat).
_ALL_DEFINITION_NAMES: frozenset[str] = frozenset(TOOL_DEFINITIONS.keys())
if HUMAN_SURFACE_MEMORY_TOOL_NAMES - _ALL_DEFINITION_NAMES:
    raise ValueError(
        "Unknown human memory tools: "
        f"{HUMAN_SURFACE_MEMORY_TOOL_NAMES - _ALL_DEFINITION_NAMES}"
    )
if HUMAN_SURFACE_CONTACT_TOOL_NAMES - _ALL_DEFINITION_NAMES:
    raise ValueError(
        "Unknown human contact tools: "
        f"{HUMAN_SURFACE_CONTACT_TOOL_NAMES - _ALL_DEFINITION_NAMES}"
    )

BASE_TOOL_NAMES: frozenset[str] = (
    ALL_TOOL_NAMES - MEMORY_TOOL_NAMES - FILE_TOOL_NAMES - TASK_TOOL_NAMES
)
CHAT_TOOL_NAMES: frozenset[str] = BASE_TOOL_NAMES - CONTACT_TOOL_NAMES
MCP_TOOL_PREFIX: str = "mcp__band__"

# AdapterFeatures category for each platform tool name. Shared across adapters
# so include_categories filtering is consistent.
_TOOL_CATEGORIES: dict[str, ToolCategory] = {
    **{name: ToolCategory.CHAT for name in CHAT_TOOL_NAMES},
    **{name: ToolCategory.CONTACTS for name in CONTACT_TOOL_NAMES},
    **{name: ToolCategory.MEMORY for name in MEMORY_TOOL_NAMES},
    **{name: ToolCategory.FILES for name in FILE_TOOL_NAMES},
    **{name: ToolCategory.TASKS for name in TASK_TOOL_NAMES},
}

# Capability -> the built-in agent+human tool names it gates. Single source
# of truth for iter_tool_definitions()/AgentTools schema methods.
CAPABILITY_TOOL_NAMES: dict[Capability, frozenset[str]] = {
    Capability.MEMORY: MEMORY_TOOL_NAMES | HUMAN_SURFACE_MEMORY_TOOL_NAMES,
    Capability.CONTACTS: CONTACT_TOOL_NAMES | HUMAN_SURFACE_CONTACT_TOOL_NAMES,
    Capability.FILES: FILE_TOOL_NAMES,
    Capability.TASKS: TASK_TOOL_NAMES,
}


# The capability set assumed when a caller passes capabilities=None to
# iter_tool_definitions()/AgentTools' schema methods. Pre-existing, unrelated
# legacy default of iter_tool_definitions itself (contact tools were never
# capability-gated before this mechanism existed) — named explicitly so it is
# never mistaken for AdapterFeatures' separately-documented opt-in-empty
# default.
DEFAULT_CAPABILITIES: frozenset[Capability] = frozenset({Capability.CONTACTS})


def resolve_capabilities(
    capabilities: frozenset[Capability] | None,
) -> frozenset[Capability]:
    """Apply the ``capabilities=None`` -> ``DEFAULT_CAPABILITIES`` default.

    Single source of truth for that resolution, shared by every call site
    that accepts an optional capability set (``iter_tool_definitions``,
    ``AgentTools.get_tool_schemas``).
    """
    return DEFAULT_CAPABILITIES if capabilities is None else capabilities


def get_band_tool_category(name: str) -> ToolCategory | None:
    """Return the AdapterFeatures category for a tool."""
    return _TOOL_CATEGORIES.get(name)


def mcp_tool_names(names: frozenset[str]) -> list[str]:
    """Convert base tool names to MCP-prefixed names for Claude SDK.

    Returns a sorted list for deterministic ordering across runs.
    """
    return [f"{MCP_TOOL_PREFIX}{name}" for name in sorted(names)]


def iter_tool_definitions(
    *,
    surface: Surface | None = Surface.AGENT,
    capabilities: frozenset[Capability] | None = None,
) -> list[ToolDefinition]:
    """Return built-in tool definitions with optional capability filtering.

    The two filters compose as independent predicates:

    - ``surface``: when not ``None``, restrict to definitions whose
      ``ToolDefinition.surface`` equals the given value. ``"agent"``
      (default) yields only agent tools, ``"human"`` yields only human
      tools, and ``None`` yields both surfaces. The default is pinned to
      ``"agent"`` so existing callers (``claude_sdk``, ``opencode``,
      ``acp``) that pipe the result straight into ``AgentTools``-shaped
      backends don't silently gain ``HumanTools``-bound entries.
    - ``capabilities``: which optional tool categories to include (see
      ``CAPABILITY_TOOL_NAMES``). A capability not in the set excludes its
      agent-surface tool names, plus its human-surface tool names for the
      capabilities that have any (memory, contacts -- files does not).
      ``None`` resolves to
      ``DEFAULT_CAPABILITIES`` -- today, contacts only, preserving this
      function's pre-existing default. The hub-room
      execution path always unions ``Capability.CONTACTS`` in regardless of
      what's passed here (see ``AgentTools.get_tool_schemas`` HUB_ROOM
      auto-enable rule).

    Args:
        surface: Optional surface filter (``"agent"`` or ``"human"``).
            Default ``"agent"``. Pass ``None`` explicitly to opt in to a
            union view across both surfaces.
        capabilities: Optional tool categories to include. ``None`` (default)
            means contacts only, for backward compatibility.
    """
    resolved = resolve_capabilities(capabilities)
    excluded: set[str] = set()
    for capability, names in CAPABILITY_TOOL_NAMES.items():
        if capability not in resolved:
            excluded |= names

    results: list[ToolDefinition] = []
    for definition in TOOL_DEFINITIONS.values():
        if surface is not None and definition.surface != surface:
            continue
        if definition.name in excluded:
            continue
        results.append(definition)
    return results


def is_mcp_content_result(data: Any) -> bool:
    """True when ``data`` is ``read_room_file``'s image-content-block shape.

    Only ``read_room_file``'s image branch returns this shape (see its
    "image" case: a ``content`` list of one ``{"type": "image", "data":
    ..., "mimeType": ...}`` block), so gate any use on that tool's name --
    prefer ``is_image_passthrough_result`` over calling this directly.
    Requires ``data``/``mimeType`` on every block (not just ``type``) so a
    malformed or future-extended block fails this check instead of reaching
    ``decode_image_block`` and raising past it.
    """
    return (
        isinstance(data, dict)
        and isinstance(data.get("content"), list)
        and bool(data["content"])
        and all(
            isinstance(block, dict)
            and block.get("type") == "image"
            and "data" in block
            and "mimeType" in block
            for block in data["content"]
        )
    )


def is_image_passthrough_result(tool_name: str, result: Any) -> bool:
    """True when ``result`` is ``band_read_room_file``'s image-block result.

    The combined check (right tool + right shape) that every adapter's
    image-passthrough branch needs -- single source of truth instead of
    re-deriving ``tool_name == BandTool.READ_ROOM_FILE and
    is_mcp_content_result(result)`` at each call site.
    """
    return tool_name == BandTool.READ_ROOM_FILE and is_mcp_content_result(result)


def image_block_placeholder(block_count: int) -> str:
    """The text an adapter emits alongside image content it passes separately."""
    return f"<{block_count} image content block(s)>"


def file_content_placeholder(byte_count: int) -> str:
    """The text a tool-call event reports in place of a file's raw content."""
    return f"<{byte_count} byte file content>"


def redact_tool_call_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Replace ``band_send_room_file``'s raw ``content`` before it reaches an event.

    Adapters that report a tool_call event by json.dumps-ing the tool's raw
    kwargs have no idea ``band_send_room_file``'s ``content`` argument can
    carry up to ``MAX_SEND_CONTENT_BYTES`` of real file bytes -- redact it
    centrally rather than teaching every adapter's generic reporter the same
    field name.
    """
    if tool_name != BandTool.SEND_ROOM_FILE or not isinstance(args, dict):
        return args
    content = args.get("content")
    if not isinstance(content, str):
        return args
    return {
        **args,
        "content": file_content_placeholder(len(content.encode("utf-8"))),
    }


def decode_image_block(block: dict[str, Any]) -> tuple[bytes, str]:
    """Decode one MCP image content block into (raw bytes, mime type)."""
    return base64.b64decode(block["data"]), block["mimeType"]
