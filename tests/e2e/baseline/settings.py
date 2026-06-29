"""Settings for the baseline testing toolkit.

Concern-separated pydantic-settings: each subclass owns one concern (Band
endpoints, Band credentials) and is composed into ``BaselineSettings``. Reuses
the existing ``BAND_*`` env vars (and ``.env.test``) so no new configuration is
required to run. Add a new subclass + nested field as new concerns appear
(model providers, pricing, etc.).
"""

from __future__ import annotations

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BandEndpoints(BaseSettings):
    """Band platform URLs."""

    model_config = SettingsConfigDict(
        env_prefix="BAND_", env_file=".env.test", extra="ignore", case_sensitive=False
    )

    # Reuse the existing BAND_BASE_URL; BAND_REST_URL is accepted as an alias.
    # Defaults to the public production host so constructing settings never
    # raises when the env vars are unset (e.g. the E2E-disabled CI gate, which
    # builds settings before it can skip). Override per environment.
    rest_url: str = Field(
        default="https://band.ai",
        validation_alias=AliasChoices("BAND_BASE_URL", "BAND_REST_URL"),
    )
    ws_url: str = "wss://band.ai/api/v1/socket/websocket"  # BAND_WS_URL


class BandCredentials(BaseSettings):
    """Band platform API keys."""

    model_config = SettingsConfigDict(
        env_prefix="BAND_", env_file=".env.test", extra="ignore", case_sensitive=False
    )

    api_key: str = ""  # BAND_API_KEY (agent / app key)
    api_key_user: str = ""  # BAND_API_KEY_USER (the test-user / driver key)
    # Optional second human user, for smokes exercising two-user interaction.
    api_key_user_2: str = ""  # BAND_API_KEY_USER_2


class BaselineRun(BaseSettings):
    """Run-level provisioning and cleanup policy."""

    model_config = SettingsConfigDict(
        env_prefix="BAND_E2E_",
        env_file=".env.test",
        extra="ignore",
        case_sensitive=False,
    )

    # Reap provisioned agents/rooms on teardown. Set false to keep resources around
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
    google_api_key: str = ""  # GOOGLE_API_KEY (Gemini Developer API)


class LLMModels(BaseSettings):
    """Model ids for the agents under test and the judge."""

    model_config = SettingsConfigDict(
        env_prefix="E2E_",
        env_file=".env.test",
        extra="ignore",
        case_sensitive=False,
    )

    # LangGraph/OpenAI agent model. Honors the documented E2E_LLM_MODEL (and
    # accepts E2E_OPENAI_MODEL as an alias).
    openai_model: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("E2E_LLM_MODEL", "E2E_OPENAI_MODEL"),
    )
    # A modern, cheap model: works for the agent under test AND for the judge,
    # which needs structured-output support (claude-3-haiku-20240307 does not).
    anthropic_model: str = "claude-haiku-4-5"  # E2E_ANTHROPIC_MODEL
    # Gemini / Google ADK agent model.
    gemini_model: str = "gemini-2.5-flash"  # E2E_GEMINI_MODEL
    # Judge model. MUST be a modern Anthropic model id (structured outputs). Left
    # blank, it falls back to ``anthropic_model`` so the judge always uses a model
    # the account has configured (E2E_JUDGE_MODEL overrides).
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

    # E2E_TIMEOUT — default seconds the waiter blocks before declaring an agent
    # stuck (a failure deadline, never a success signal).
    e2e_timeout: int = 60

    endpoints: BandEndpoints = Field(default_factory=BandEndpoints)
    credentials: BandCredentials = Field(default_factory=BandCredentials)
    run: BaselineRun = Field(default_factory=BaselineRun)
    llm_credentials: LLMCredentials = Field(default_factory=LLMCredentials)
    llm_models: LLMModels = Field(default_factory=LLMModels)
