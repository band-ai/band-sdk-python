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
    which holds per-agent-instance tunables.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    # AgentTools._fetch_attachment's cache size (band.runtime.tools) -- one
    # process-wide budget, not "N per room".
    BAND_ATTACHMENT_CACHE_MAXSIZE: int = 1000
