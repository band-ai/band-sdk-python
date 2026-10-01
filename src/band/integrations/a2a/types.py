"""A2A integration types."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field

from band.core.adapterconfig import BaseAdapterConfig


class A2AAuth(BaseAdapterConfig):
    """Authentication configuration for A2A agent.

    Supports multiple authentication methods that can be combined:
    - api_key: API key authentication (header: X-API-Key or similar)
    - bearer_token: Bearer token authentication (header: Authorization: Bearer <token>)
    - headers: Custom headers for authentication

    Example:
        # API key auth
        auth = A2AAuth(api_key="my-secret-key")

        # Bearer token
        auth = A2AAuth(bearer_token="eyJ...")

        # Custom headers
        auth = A2AAuth(headers={"X-Custom-Auth": "value"})
    """

    api_key: str | None = None
    bearer_token: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    def to_headers(self) -> dict[str, str]:
        """Convert auth config to HTTP headers."""
        result = dict(self.headers)

        if self.api_key:
            result["X-API-Key"] = self.api_key

        if self.bearer_token:
            result["Authorization"] = f"Bearer {self.bearer_token}"

        return result


class A2AAdapterConfig(BaseAdapterConfig):
    """Settings for bridging a Band agent to a remote A2A agent.

    Attributes:
        remote_url: Base URL of the remote A2A agent.
        auth: Credentials sent with every request to the remote agent.
        streaming: Whether to use streaming mode (SSE) for responses.
    """

    remote_url: str
    auth: A2AAuth | None = None
    streaming: bool = True


@dataclass
class A2ASessionState:
    """Session state extracted from platform history.

    Used by A2AHistoryConverter to restore A2A session state
    when an agent rejoins a chat room.

    Attributes:
        context_id: A2A context ID for conversation continuity.
        task_id: Last known task ID (for resumption).
        task_state: Last known task state name (e.g., "TASK_STATE_INPUT_REQUIRED";
            history from the pre-protobuf adapter holds 0.x values like
            "input-required").
    """

    context_id: str | None = None
    task_id: str | None = None
    task_state: str | None = None
