"""Settings for the baseline testing toolkit.

Concern-separated pydantic-settings: each subclass owns one concern (Band
endpoints, Band credentials) and is composed into ``BaselineSettings``. Reuses
the existing ``BAND_*`` env vars (and ``.env.test``) so no new configuration is
required to run. Add a new subclass + nested field as new concerns appear
(model providers, pricing, etc.).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env.test"


class BandEndpoints(BaseSettings):
    """Band platform URLs."""

    model_config = SettingsConfigDict(
        env_prefix="BAND_", env_file=_ENV_FILE, extra="ignore", case_sensitive=False
    )

    base_url: str = "http://localhost:4000"  # BAND_BASE_URL
    ws_url: str = "ws://localhost:4000/api/v1/socket/websocket"  # BAND_WS_URL


class BandCredentials(BaseSettings):
    """Band platform API keys."""

    model_config = SettingsConfigDict(
        env_prefix="BAND_", env_file=_ENV_FILE, extra="ignore", case_sensitive=False
    )

    api_key: str = ""  # BAND_API_KEY (agent / app key)
    api_key_user: str = ""  # BAND_API_KEY_USER (the test-user / driver key)


class BaselineSettings(BaseSettings):
    """Top-level baseline toolkit config, composed from per-concern groups."""

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    endpoints: BandEndpoints = Field(default_factory=BandEndpoints)
    credentials: BandCredentials = Field(default_factory=BandCredentials)
