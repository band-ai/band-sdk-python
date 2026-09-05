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
    RestClient,
    AsyncRestClient,
    AgentContact,
    AgentMe,
    AgentMemory,
    Attachment,
    Board,
    ChatMessageRequest,
    ChatEventRequest,
    ChatRoomRequest,
    ParticipantRequest,
    AgentMemoryCreateRequest,
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
    NotFoundError,
    Peer,
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
    "RestClient",
    "AsyncRestClient",
    "aclose_rest_client",
    "AgentContact",
    "AgentMe",
    "AgentMemory",
    "Attachment",
    "ChatMessageRequest",
    "ChatMessageRequestMentionsItem",
    "ChatEventRequest",
    "ChatRoomRequest",
    "ParticipantRequest",
    "AgentMemoryCreateRequest",
    "Board",
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
    "NotFoundError",
    "ParsingError",
    "Peer",
    "Task",
    "TaskActor",
    "UnauthorizedError",
    "UnprocessableEntityError",
    "RequestOptions",
    "DEFAULT_REQUEST_OPTIONS",
]
