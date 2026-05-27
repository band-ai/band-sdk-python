"""Kore.ai integration types and configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class KoreAIConfig:
    """Configuration for the Kore.ai adapter.

    All required fields can be provided via constructor or environment variables.
    Constructor values take precedence over environment variables.

    Attributes:
        bot_id: Kore.ai Bot ID (env: KOREAI_BOT_ID).
        client_id: Kore.ai Client ID from webhook channel config (env: KOREAI_CLIENT_ID).
        client_secret: Kore.ai Client Secret for JWT signing (env: KOREAI_CLIENT_SECRET).
        callback_url: Public URL where Kore.ai sends bot responses.
        api_host: Kore.ai API host.
        jwt_algorithm: JWT signing algorithm (HS256 or HS512).
        callback_port: Port for the callback HTTP server.
        callback_bind_host: Bind address for the callback server.
        response_timeout_seconds: Max seconds to wait for Kore.ai callback(s).
        session_timeout_seconds: Session inactivity timeout (must match Kore.ai config).
        webhook_secret: Reserved for future verified Kore.ai callback signing.
            Configuring it currently makes KoreAIAdapter fail fast because the
            live callback auth contract has not been validated.
    """

    bot_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    callback_url: str = ""
    api_host: str = "https://bots.kore.ai"
    jwt_algorithm: str = "HS256"
    callback_port: int = 3100
    callback_bind_host: str = "127.0.0.1"
    response_timeout_seconds: int = 120
    session_timeout_seconds: int = 900
    webhook_secret: str | None = None

    def __post_init__(self) -> None:
        """Resolve env var fallbacks and validate required fields."""
        if not self.bot_id:
            self.bot_id = os.environ.get("KOREAI_BOT_ID", "")
        if not self.client_id:
            self.client_id = os.environ.get("KOREAI_CLIENT_ID", "")
        if not self.client_secret:
            self.client_secret = os.environ.get("KOREAI_CLIENT_SECRET", "")
        if not self.callback_url:
            self.callback_url = os.environ.get("KOREAI_CALLBACK_URL", "")

        _missing: list[str] = []
        if not self.bot_id:
            _missing.append("bot_id (or KOREAI_BOT_ID)")
        if not self.client_id:
            _missing.append("client_id (or KOREAI_CLIENT_ID)")
        if not self.client_secret:
            _missing.append("client_secret (or KOREAI_CLIENT_SECRET)")
        if not self.callback_url:
            _missing.append("callback_url (or KOREAI_CALLBACK_URL)")
        if _missing:
            raise ValueError(
                "Missing required Kore.ai configuration: %s" % ", ".join(_missing)
            )

        if self.jwt_algorithm not in ("HS256", "HS512"):
            raise ValueError(
                "jwt_algorithm must be 'HS256' or 'HS512', got: %s" % self.jwt_algorithm
            )


@dataclass
class KoreAISessionState:
    """Session state extracted from platform history.

    Used by KoreAIHistoryConverter to restore session state
    when an adapter process restarts or rejoins a room.

    Attributes:
        koreai_identity: The from.id used for this room (= Thenvoi room ID).
        koreai_last_activity: Unix timestamp of last message sent to Kore.ai.
    """

    koreai_identity: str | None = None
    koreai_last_activity: float | None = None


@dataclass
class KoreAIRoomState:
    """Per-room runtime state tracked by the adapter.

    Attributes:
        from_id: The from.id sent to Kore.ai (= Thenvoi room ID).
        last_activity: Unix timestamp of last outbound message.
        is_new_session: Whether the next message should force a new Kore.ai session.
            Defaults to False because Kore.ai auto-creates a session for a new
            from.id. Only set to True after session timeout or explicit reset.
    """

    from_id: str
    last_activity: float | None = None
    is_new_session: bool = False


@dataclass
class CallbackData:
    """Parsed callback data from Kore.ai.

    Attributes:
        messages: Text messages extracted from callbacks.
        task_completed: Whether a task-completion callback was received.
        end_reason: The endReason from task completion.
        task_name: The completedTaskName from task completion.
        is_agent_transfer: Whether endReason indicates agent transfer.
    """

    messages: list[str] = field(default_factory=list)
    task_completed: bool = False
    end_reason: str = ""
    task_name: str = ""
    is_agent_transfer: bool = False
