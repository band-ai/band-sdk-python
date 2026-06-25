"""Settings for the baseline testing toolkit.

Concern-separated pydantic-settings: each subclass owns one concern (Band
endpoints, Band credentials) and is composed into ``BaselineSettings``. Reuses
the existing ``BAND_*`` env vars (and ``.env.test``) so no new configuration is
required to run. Add a new subclass + nested field as new concerns appear
(model providers, pricing, etc.).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BandEndpoints(BaseSettings):
    """Band platform URLs."""

    model_config = SettingsConfigDict(
        env_prefix="BAND_", env_file=".env.test", extra="ignore", case_sensitive=False
    )

    rest_url: str = "http://localhost:4000"  # BAND_REST_URL
    ws_url: str = (
        "wss://platform.dev.band.ai/api/v1/socket/websocket"  # BAND_WS_URL
    )


class BandCredentials(BaseSettings):
    """Band platform API keys."""

    model_config = SettingsConfigDict(
        env_prefix="BAND_", env_file=".env.test", extra="ignore", case_sensitive=False
    )

    api_key: str = ""  # BAND_API_KEY (agent / app key)
    api_key_user: str = ""  # BAND_API_KEY_USER (the test-user / driver key)


class BaselineSettings(BaseSettings):
    """Top-level baseline toolkit config, composed from per-concern groups."""

    model_config = SettingsConfigDict(env_file=".env.test", extra="ignore")

    endpoints: BandEndpoints = Field(default_factory=BandEndpoints)
    credentials: BandCredentials = Field(default_factory=BandCredentials)
