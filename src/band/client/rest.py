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

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from json.decoder import JSONDecodeError
from typing import Any

from band_rest import (
    RestClient,
    AsyncRestClient,
    AgentContact,
    AgentMe,
    AgentMemory,
    ChatMessageRequest,
    ChatEventRequest,
    ChatRoomRequest,
    ParticipantRequest,
    AgentMemoryCreateRequest,
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
    NotFoundError,
    Peer,
    UnauthorizedError,
)
from band_rest.agent_api_identity.raw_client import AsyncRawAgentApiIdentityClient
from band_rest.core import ParsingError
from band_rest.core.api_error import ApiError
from band_rest.core.request_options import RequestOptions
from band_rest.types import ChatMessageRequestMentionsItem

# Default request options with retry enabled for rate limiting (HTTP 429)
# The band_rest client defaults to max_retries=0, which disables retries.
# We set max_retries=3 to handle transient rate limit errors gracefully.
DEFAULT_REQUEST_OPTIONS: RequestOptions = {"max_retries": 3}


def _without_json_decode_chain(
    method: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Re-raise Fern ``ApiError`` without a ``JSONDecodeError`` ``__context__``.

    ``band-client-rest==0.0.26`` (still in 0.0.28) calls ``_response.json()``
    for non-2xx statuses, then ``raise ApiError(...)`` on ``JSONDecodeError``
    without ``from None``. Empty ALB 429 bodies therefore print both
    tracebacks. Drop this wrap when the pin raises with ``from None``.
    """

    @wraps(method)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await method(*args, **kwargs)
        except ApiError as error:
            if isinstance(error.__context__, JSONDecodeError):
                raise error from None
            raise

    return wrapper


AsyncRawAgentApiIdentityClient.get_agent_me = _without_json_decode_chain(
    AsyncRawAgentApiIdentityClient.get_agent_me
)

__all__ = [
    "RestClient",
    "AsyncRestClient",
    "AgentContact",
    "AgentMe",
    "AgentMemory",
    "ChatMessageRequest",
    "ChatMessageRequestMentionsItem",
    "ChatEventRequest",
    "ChatRoomRequest",
    "ParticipantRequest",
    "AgentMemoryCreateRequest",
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
    "NotFoundError",
    "ParsingError",
    "Peer",
    "UnauthorizedError",
    "RequestOptions",
    "DEFAULT_REQUEST_OPTIONS",
]
