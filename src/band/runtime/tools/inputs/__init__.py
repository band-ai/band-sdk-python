"""Every built-in Band tool's input model, grouped by domain.

Re-exports the full set so ``registry.py`` (which wires each model into a
``ToolDefinition``) can import from this one place instead of every submodule.
"""

from __future__ import annotations

from band.runtime.tools.inputs.chat import (
    AddParticipantInput,
    CreateChatroomInput,
    GetParticipantsInput,
    LookupPeersInput,
    RemoveParticipantInput,
    SendEventInput,
    SendMessageInput,
)
from band.runtime.tools.inputs.contacts import (
    AddContactInput,
    ListContactRequestsInput,
    ListContactsInput,
    RemoveContactInput,
    RespondContactRequestInput,
)
from band.runtime.tools.inputs.files import (
    ListRoomFilesInput,
    ReadRoomFileInput,
    SendRoomFileInput,
)
from band.runtime.tools.inputs.human_agents import (
    ListMyAgentsInput,
    RegisterMyAgentInput,
)
from band.runtime.tools.inputs.human_chats import (
    CreateMyChatRoomInput,
    GetMyChatRoomInput,
    ListMyChatsInput,
)
from band.runtime.tools.inputs.human_contacts import (
    ApproveContactRequestInput,
    CancelContactRequestInput,
    CreateContactRequestInput,
    ListMyContactsInput,
    ListReceivedContactRequestsInput,
    ListSentContactRequestsInput,
    RejectContactRequestInput,
    RemoveMyContactInput,
    ResolveHandleInput,
)
from band.runtime.tools.inputs.human_memories import (
    ArchiveUserMemoryInput,
    DeleteUserMemoryInput,
    GetUserMemoryInput,
    ListUserMemoriesInput,
    RestoreUserMemoryInput,
    SupersedeUserMemoryInput,
)
from band.runtime.tools.inputs.human_messages import (
    ListMyChatMessagesInput,
    SendMyChatMessageInput,
)
from band.runtime.tools.inputs.human_participants import (
    AddMyChatParticipantInput,
    ListMyChatParticipantsInput,
    RemoveMyChatParticipantInput,
)
from band.runtime.tools.inputs.human_profile import (
    GetMyProfileInput,
    ListMyPeersInput,
    UpdateMyProfileInput,
)
from band.runtime.tools.inputs.memory import (
    ArchiveMemoryInput,
    GetMemoryInput,
    ListMemoriesInput,
    StoreMemoryInput,
    SupersedeMemoryInput,
)
from band.runtime.tools.inputs.tasks import (
    CreateTaskInput,
    GetBoardInput,
    GetTaskHistoryInput,
    GetTaskInput,
    ListTasksInput,
    SetBoardInput,
    UpdateTaskInput,
)

__all__ = [
    "AddContactInput",
    "AddMyChatParticipantInput",
    "AddParticipantInput",
    "ApproveContactRequestInput",
    "ArchiveMemoryInput",
    "ArchiveUserMemoryInput",
    "CancelContactRequestInput",
    "CreateChatroomInput",
    "CreateContactRequestInput",
    "CreateMyChatRoomInput",
    "CreateTaskInput",
    "DeleteUserMemoryInput",
    "GetBoardInput",
    "GetMemoryInput",
    "GetMyChatRoomInput",
    "GetMyProfileInput",
    "GetParticipantsInput",
    "GetTaskHistoryInput",
    "GetTaskInput",
    "GetUserMemoryInput",
    "ListContactRequestsInput",
    "ListContactsInput",
    "ListMemoriesInput",
    "ListMyAgentsInput",
    "ListMyChatMessagesInput",
    "ListMyChatParticipantsInput",
    "ListMyChatsInput",
    "ListMyContactsInput",
    "ListMyPeersInput",
    "ListReceivedContactRequestsInput",
    "ListRoomFilesInput",
    "ListSentContactRequestsInput",
    "ListTasksInput",
    "ListUserMemoriesInput",
    "LookupPeersInput",
    "ReadRoomFileInput",
    "RegisterMyAgentInput",
    "RejectContactRequestInput",
    "RemoveContactInput",
    "RemoveMyChatParticipantInput",
    "RemoveMyContactInput",
    "RemoveParticipantInput",
    "ResolveHandleInput",
    "RespondContactRequestInput",
    "RestoreUserMemoryInput",
    "SendEventInput",
    "SendMessageInput",
    "SendMyChatMessageInput",
    "SendRoomFileInput",
    "SetBoardInput",
    "StoreMemoryInput",
    "SupersedeMemoryInput",
    "SupersedeUserMemoryInput",
    "UpdateMyProfileInput",
    "UpdateTaskInput",
]
