"""A2A integration types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class A2AAuth:
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
    headers: dict[str, str] = field(default_factory=dict)

    def to_headers(self) -> dict[str, str]:
        """Convert auth config to HTTP headers."""
        result = dict(self.headers)

        if self.api_key:
            result["X-API-Key"] = self.api_key

        if self.bearer_token:
            result["Authorization"] = f"Bearer {self.bearer_token}"

        return result


@dataclass
class A2ASessionState:
    """Session state extracted from platform history.

    Used by A2AHistoryConverter to restore A2A session state
    when an agent rejoins a chat room.

    Attributes:
        context_id: A2A context ID for conversation continuity.
        task_id: Last known task ID (for resumption).
        task_state: Last known task state as string (e.g., "input_required").
    """

    context_id: str | None = None
    task_id: str | None = None
    task_state: str | None = None
