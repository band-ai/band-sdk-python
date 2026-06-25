"""Settings for the baseline testing toolkit.

Concern-separated pydantic-settings: each subclass owns one concern (Band
endpoints, Band credentials) and is composed into ``BaselineSettings``. Reuses
the existing ``BAND_*`` env vars (and ``.env.test``) so no new configuration is
required to run. Add a new subclass + nested field as new concerns appear
(model providers, pricing, etc.).
"""

from __future__ import annotations

from pydantic import Field, model_validator
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


class LLMCredentials(BaseSettings):
    """Model-provider API keys (standard provider env-var names, no prefix)."""

    model_config = SettingsConfigDict(
        env_file=".env.test", extra="ignore", case_sensitive=False
    )

    openai_api_key: str = ""  # OPENAI_API_KEY
    anthropic_api_key: str = ""  # ANTHROPIC_API_KEY


class LLMModels(BaseSettings):
    """Model ids for the agents under test and the judge."""

    model_config = SettingsConfigDict(
        env_prefix="E2E_",
        env_file=".env.test",
        extra="ignore",
        case_sensitive=False,
    )

    openai_model: str = "gpt-4o-mini"  # E2E_OPENAI_MODEL (LangGraph agent)
    anthropic_model: str = "claude-3-haiku-20240307"  # E2E_ANTHROPIC_MODEL
    # Judge model. Left blank, it falls back to ``anthropic_model`` so the judge
    # always uses a model the account has configured (E2E_JUDGE_MODEL overrides).
    judge_model: str = ""  # E2E_JUDGE_MODEL

    @model_validator(mode="after")
    def _default_judge_to_anthropic(self) -> LLMModels:
        if not self.judge_model:
            self.judge_model = self.anthropic_model
        return self


class BaselineSettings(BaseSettings):
    """Top-level baseline toolkit config, composed from per-concern groups."""

    model_config = SettingsConfigDict(env_file=".env.test", extra="ignore")

    # E2E_TESTS_ENABLED — the master gate for the live baseline suite.
    e2e_tests_enabled: bool = False

    endpoints: BandEndpoints = Field(default_factory=BandEndpoints)
    credentials: BandCredentials = Field(default_factory=BandCredentials)
    run: BaselineRun = Field(default_factory=BaselineRun)
    llm_credentials: LLMCredentials = Field(default_factory=LLMCredentials)
    llm_models: LLMModels = Field(default_factory=LLMModels)
