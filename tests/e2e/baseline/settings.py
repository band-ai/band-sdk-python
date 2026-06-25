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
    ws_url: str = "ws://localhost:4000/api/v1/socket/websocket"  # BAND_WS_URL


class BandCredentials(BaseSettings):
    """Band platform API keys."""

    model_config = SettingsConfigDict(
        env_prefix="BAND_", env_file=".env.test", extra="ignore", case_sensitive=False
    )

    api_key: str = ""  # BAND_API_KEY (agent / app key)
    api_key_user: str = ""  # BAND_API_KEY_USER (the test-user / driver key)


class BaselineRun(BaseSettings):
    """Run-level provisioning and cleanup policy."""

    model_config = SettingsConfigDict(
        env_prefix="BAND_E2E_",
        env_file=".env.test",
        extra="ignore",
        case_sensitive=False,
    )

    # Reap minted agents/rooms on teardown. Set false to keep resources around
    # for on-purpose debugging of a failing run.
    autoclean: bool = True  # BAND_E2E_AUTOCLEAN
    # Run the prefix-guarded orphan sweep once at session start.
    orphan_sweep: bool = True  # BAND_E2E_ORPHAN_SWEEP
    # Safety guard: the sweep only reaps agents older than this, so a
    # concurrent run on the same shared platform is never deleted mid-flight.
    orphan_max_age_minutes: int = 120  # BAND_E2E_ORPHAN_MAX_AGE_MINUTES


class BaselineSettings(BaseSettings):
    """Top-level baseline toolkit config, composed from per-concern groups."""

    model_config = SettingsConfigDict(env_file=".env.test", extra="ignore")

    endpoints: BandEndpoints = Field(default_factory=BandEndpoints)
    credentials: BandCredentials = Field(default_factory=BandCredentials)
    run: BaselineRun = Field(default_factory=BaselineRun)
