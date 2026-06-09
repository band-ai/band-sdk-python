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

from thenvoi_rest import (
    RestClient,
    AsyncRestClient,
    AgentMe,
    ChatMessageRequest,
    ChatEventRequest,
    ChatRoomRequest,
    ParticipantRequest,
    MemoryCreateRequest,
    NotFoundError,
    UnauthorizedError,
)
from thenvoi_rest.core.request_options import RequestOptions
from thenvoi_rest.types import ChatMessageRequestMentionsItem

# Default request options with retry enabled for rate limiting (HTTP 429)
# The thenvoi_rest client defaults to max_retries=0, which disables retries.
# We set max_retries=3 to handle transient rate limit errors gracefully.
DEFAULT_REQUEST_OPTIONS: RequestOptions = {"max_retries": 3}

__all__ = [
    "RestClient",
    "AsyncRestClient",
    "AgentMe",
    "ChatMessageRequest",
    "ChatMessageRequestMentionsItem",
    "ChatEventRequest",
    "ChatRoomRequest",
    "ParticipantRequest",
    "MemoryCreateRequest",
    "NotFoundError",
    "UnauthorizedError",
    "RequestOptions",
    "DEFAULT_REQUEST_OPTIONS",
]
