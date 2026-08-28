"""Platform connection settings resolved from the environment.

Single source of truth for the Band platform URLs: explicit arguments win,
then ``BAND_WS_URL`` / ``BAND_REST_URL`` from the environment, then the
production defaults. ``Agent.create`` resolves through here so applications
and examples never hand-roll ``os.getenv`` + validation boilerplate.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_WS_URL = "wss://app.band.ai/api/v1/socket/websocket"
DEFAULT_REST_URL = "https://app.band.ai"


class PlatformSettings(BaseSettings):
    """Band platform URLs, overridable via environment variables."""

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    BAND_WS_URL: str = DEFAULT_WS_URL
    BAND_REST_URL: str = DEFAULT_REST_URL


class RuntimeSettings(BaseSettings):
    """Process-wide runtime tuning knobs, overridable via environment
    variables. Distinct from ``SessionConfig`` (``band.runtime.types``),
    which holds per-agent-instance tunables passed explicitly by the caller
    constructing that agent -- these are knobs for state that is itself
    process-wide (e.g. a module-level cache shared by every agent/room in
    the process), so a per-agent config object is the wrong scope for them.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    # AgentTools._find_attachment's room-scoped lookup cache (see
    # band.runtime.tools). Shared across every room/agent in the process, so
    # this is one process-wide budget, not "N per room".
    BAND_ATTACHMENT_CACHE_MAXSIZE: int = 1000
