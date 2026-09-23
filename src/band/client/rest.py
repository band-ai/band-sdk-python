"""
Re-export wrapper for Band REST API client.

Usage:
    from band.client.rest import AsyncRestClient, DEFAULT_REQUEST_OPTIONS

    async_client = AsyncRestClient(api_key="your-api-key")

    # All REST API calls should include request_options for retry on HTTP 429:
    response = await async_client.agent_api_chats.some_method(
        ...,
        request_options=DEFAULT_REQUEST_OPTIONS,
    )
"""

from band_rest import (
    AddAgentContactResponseData,
    AgentContact,
    AgentMe,
    AgentMemory,
    AgentMemoryCreateRequest,
    AsyncRestClient,
    Attachment,
    Board,
    ChatEventRequest,
    ChatMessage,
    ChatMessageRequest,
    ChatParticipant,
    ChatRoomRequest,
    EventCreatedResponse,
    GetChatTaskHistoryResponse,
    GetChatTaskHistoryResponseMetadata,
    ListAgentContactRequestsResponse,
    ListAgentContactRequestsResponseData,
    ListAgentContactRequestsResponseMetadata,
    ListAgentContactRequestsResponseMetadataReceived,
    ListAgentContactRequestsResponseMetadataSent,
    ListAgentContactsResponse,
    ListAgentContactsResponseMetadata,
    ListAgentMemoriesResponse,
    ListAgentMemoriesResponseMeta,
    ListAgentPeersResponse,
    ListAgentPeersResponseMetadata,
    ListChatTasksResponse,
    ListChatTasksResponseMetadata,
    MessageSentResponse,
    MessageSentResponseRecipientsItem,
    NotFoundError,
    ParticipantRequest,
    Peer,
    ReceivedContactRequest,
    RemoveAgentContactResponseData,
    RespondToAgentContactRequestResponseData,
    RestClient,
    SentContactRequest,
    Task,
    TaskActor,
    UnauthorizedError,
    UnprocessableEntityError,
)
from band_rest.core import ParsingError
from band_rest.core.request_options import RequestOptions
from band_rest.types import ChatMessageRequestMentionsItem

# Default request options with retry enabled for rate limiting (HTTP 429)
# The band_rest client defaults to max_retries=0, which disables retries.
# We set max_retries=3 to handle transient rate limit errors gracefully.
DEFAULT_REQUEST_OPTIONS: RequestOptions = {"max_retries": 3}


async def aclose_rest_client(client: AsyncRestClient) -> None:
    """Close ``client``'s underlying httpx client.

    Fern's generated wrapper buries the real httpx client three attributes
    deep (``_client_wrapper.httpx_client.httpx_client``) -- one place to
    reach through that chain so a future ``band_rest`` upgrade only needs
    updating here.
    """
    await client._client_wrapper.httpx_client.httpx_client.aclose()


__all__ = [
    "DEFAULT_REQUEST_OPTIONS",
    "AddAgentContactResponseData",
    "AgentContact",
    "AgentMe",
    "AgentMemory",
    "AgentMemoryCreateRequest",
    "AsyncRestClient",
    "Attachment",
    "Board",
    "ChatEventRequest",
    "ChatMessage",
    "ChatMessageRequest",
    "ChatMessageRequestMentionsItem",
    "ChatParticipant",
    "ChatRoomRequest",
    "EventCreatedResponse",
    "GetChatTaskHistoryResponse",
    "GetChatTaskHistoryResponseMetadata",
    "ListAgentContactRequestsResponse",
    "ListAgentContactRequestsResponseData",
    "ListAgentContactRequestsResponseMetadata",
    "ListAgentContactRequestsResponseMetadataReceived",
    "ListAgentContactRequestsResponseMetadataSent",
    "ListAgentContactsResponse",
    "ListAgentContactsResponseMetadata",
    "ListAgentMemoriesResponse",
    "ListAgentMemoriesResponseMeta",
    "ListAgentPeersResponse",
    "ListAgentPeersResponseMetadata",
    "ListChatTasksResponse",
    "ListChatTasksResponseMetadata",
    "MessageSentResponse",
    "MessageSentResponseRecipientsItem",
    "NotFoundError",
    "ParsingError",
    "ParticipantRequest",
    "Peer",
    "ReceivedContactRequest",
    "RemoveAgentContactResponseData",
    "RequestOptions",
    "RespondToAgentContactRequestResponseData",
    "RestClient",
    "SentContactRequest",
    "Task",
    "TaskActor",
    "UnauthorizedError",
    "UnprocessableEntityError",
    "aclose_rest_client",
]
