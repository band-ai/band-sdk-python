"""Settings for the baseline testing toolkit.

Concern-separated pydantic-settings: each subclass owns one concern (Band
endpoints, Band credentials) and is composed into ``BaselineSettings``. Reuses
the existing ``BAND_*`` env vars (and ``.env.test``) so no new configuration is
required to run. Add a new subclass + nested field as new concerns appear
(model providers, pricing, etc.).
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env.test into os.environ (idempotent, non-overriding) so the framework
# LLM SDKs (anthropic, openai, google-genai) that read their provider keys
# straight from the environment pick them up. pydantic-settings' ``env_file``
# only populates this module's own fields, not ``os.environ``, so the SDKs — and
# any adapter/model the toolkit builds — need this explicit load. Imported before
# any fixture constructs a client or adapter, so keys are present in time.
load_dotenv(Path(__file__).parents[3] / ".env.test", override=False)


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
    """Run-level scoping, provisioning, and cleanup policy."""

    model_config = SettingsConfigDict(
        env_prefix="BAND_E2E_",
        env_file=".env.test",
        extra="ignore",
        case_sensitive=False,
    )

    # CI-lane scoping: restrict collection to one lane's adapters (a CI job is a
    # uv extra + optional backend setup). Adapters outside the lane skip-with-
    # reason; in-lane adapters stay fail-loud. Empty = full matrix, the local
    # default.
    lane: str = ""  # BAND_E2E_LANE
    # Reap provisioned agents/rooms on teardown. Set false to keep resources around
    # for on-purpose debugging of a failing run.
    autoclean: bool = True  # BAND_E2E_AUTOCLEAN
    # Run the prefix-guarded orphan sweep once at session start.
    orphan_sweep: bool = True  # BAND_E2E_ORPHAN_SWEEP
    # Safety guard: the sweep only reaps agents older than this, so a
    # concurrent run on the same shared platform is never deleted mid-flight.
    orphan_max_age_minutes: int = 120  # BAND_E2E_ORPHAN_MAX_AGE_MINUTES


class LLMCredentials(BaseSettings):
    """Model-provider API keys / config (standard provider env-var names, no prefix)."""

    model_config = SettingsConfigDict(
        env_file=".env.test", extra="ignore", case_sensitive=False
    )

    openai_api_key: str = ""  # OPENAI_API_KEY
    anthropic_api_key: str = ""  # ANTHROPIC_API_KEY
    google_api_key: str = ""  # GOOGLE_API_KEY (Gemini Developer API)
    gemini_api_key: str = ""  # GEMINI_API_KEY (alternative Gemini key var)
    # Vertex AI config (an alternative to a Gemini Developer key).
    google_genai_use_vertexai: str = ""  # GOOGLE_GENAI_USE_VERTEXAI ("true"/"")
    google_cloud_project: str = ""  # GOOGLE_CLOUD_PROJECT


class Backends(BaseSettings):
    """Config for the external-backend adapters (codex / opencode / letta / copilot_sdk).

    Reads each backend's standard env vars (no shared prefix). Defaults that the
    matrix relies on live here -- the single source -- not scattered through the
    adapter builders or the requirement predicates.
    """

    model_config = SettingsConfigDict(
        env_file=".env.test", extra="ignore", case_sensitive=False
    )

    # Codex CLI (stdio app-server).
    codex_command: str = ""  # CODEX_COMMAND (override the `codex` binary + args)
    codex_cwd: str = ""  # CODEX_CWD (a disposable working dir outside the repo)
    codex_cwd_is_disposable: bool = Field(
        default=False, validation_alias="E2E_CODEX_CWD_IS_DISPOSABLE"
    )
    codex_model: str = ""  # CODEX_MODEL (else falls back to the OpenAI model)

    # OpenCode server.
    opencode_base_url: str = ""  # OPENCODE_BASE_URL (a running `opencode serve`)
    opencode_provider_id: str = "opencode"  # OPENCODE_PROVIDER_ID (the Zen provider)
    # A current OpenCode Zen *free* model (the catalogue shifts; confirm against the
    # server's /config/providers). Overridable via OPENCODE_MODEL_ID.
    opencode_model_id: str = "mimo-v2.5-free"  # OPENCODE_MODEL_ID

    # Letta (Cloud or self-hosted). The Letta server executes platform tools by
    # calling a Band MCP server: by default the adapter self-hosts one in-process
    # (advertised to the dockerized Letta via LETTA_MCP_ADVERTISED_HOST); setting
    # MCP_SERVER_URL switches the builder to an external band-mcp instead.
    letta_base_url: str = "https://api.letta.com"  # LETTA_BASE_URL
    letta_api_key: str = ""  # LETTA_API_KEY (Letta Cloud)
    letta_model: str = "openai/gpt-5.4-mini"  # LETTA_MODEL
    # Letta's docker server requires an embedding model on agent create.
    letta_embedding: str = "openai/text-embedding-3-small"  # LETTA_EMBEDDING
    # Host the (dockerized) Letta server uses to reach the adapter's self-hosted
    # MCP server. host.docker.internal works on CI (docker run --add-host) and on
    # macOS/Windows Docker Desktop; set 127.0.0.1 for a natively-run Letta.
    letta_mcp_advertised_host: str = "host.docker.internal"  # LETTA_MCP_ADVERTISED_HOST
    mcp_server_url: str = ""  # MCP_SERVER_URL (external band-mcp SSE endpoint)

    # Copilot SDK. BYOK inference reuses llm_credentials.anthropic_api_key; this is
    # only the runtime-auth token, from a GitHub account with Copilot entitlement.
    github_token: str = ""  # GITHUB_TOKEN


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
        default="gpt-5.4-mini",
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
    # stuck (a failure deadline, never a success signal). Frameworks vary widely
    # in cold-start + round-trip latency (crewai crew construction, self-hosted
    # backends, free/slow models), so the budget is generous by default.
    e2e_timeout: int = 120

    endpoints: BandEndpoints = Field(default_factory=BandEndpoints)
    credentials: BandCredentials = Field(default_factory=BandCredentials)
    run: BaselineRun = Field(default_factory=BaselineRun)
    llm_credentials: LLMCredentials = Field(default_factory=LLMCredentials)
    llm_models: LLMModels = Field(default_factory=LLMModels)
    backends: Backends = Field(default_factory=Backends)
