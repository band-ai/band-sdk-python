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
