"""Reusable pydantic-settings groups for E2E configuration.

Leaf module with no dependency on ``tests.e2e.conftest`` so it can be imported
by both ``conftest`` (to extend ``E2ESettings``) and ``baseline_settings``
without an import cycle.

Each group is a ``BaseSettings`` subclass with its own ``env_prefix``; nesting
one as a field (via ``Field(default_factory=...)``) lets the nested group read
its own environment variables independently. Field name maps to the suffixed
environment variable, e.g. ``EchoAgentSettings.id`` reads ``E2E_ECHO_AGENT_ID``.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LiveAgentSettings(BaseSettings):
    """Identity + credentials for a real platform agent used in a live scenario.

    Subclasses set ``env_prefix`` so the same field shape maps to a different
    group of environment variables (Echo, the L3 trio, the L4 frameworks).
    """

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    id: str = ""
    api_key: str = ""
    name: str = ""
    handle: str = ""
    description: str = ""

    @property
    def visible_handle(self) -> str:
        """Handle without a leading ``@`` (callers add it back where needed)."""
        return self.handle.lstrip("@")

    def missing_env_names(self, required: tuple[str, ...]) -> list[str]:
        """Return the environment-variable names of empty *required* fields.

        Reconstructs the suffixed env name (``<env_prefix><FIELD>``) so skip
        messages match the variables a developer would actually set.
        """
        prefix = self.model_config.get("env_prefix", "")
        return [
            f"{prefix}{field.upper()}" for field in required if not getattr(self, field)
        ]


# --- L0 / L4 Echo companion -------------------------------------------------


class EchoAgentSettings(LiveAgentSettings):
    model_config = SettingsConfigDict(
        env_prefix="E2E_ECHO_AGENT_", case_sensitive=False, extra="ignore"
    )


# --- L3 multi-participant trio ----------------------------------------------


class L3TestAgentSettings(LiveAgentSettings):
    model_config = SettingsConfigDict(
        env_prefix="E2E_L3_TEST_AGENT_", case_sensitive=False, extra="ignore"
    )


class L3CalcAgentSettings(LiveAgentSettings):
    model_config = SettingsConfigDict(
        env_prefix="E2E_L3_CALC_AGENT_", case_sensitive=False, extra="ignore"
    )


class L3GreeterAgentSettings(LiveAgentSettings):
    model_config = SettingsConfigDict(
        env_prefix="E2E_L3_GREETER_AGENT_", case_sensitive=False, extra="ignore"
    )


# Note: the L4 per-framework agent identities (E2E_<FRAMEWORK>_AGENT_*) are
# resolved through the generic per-adapter credential lookup
# (``e2e_adapter_agent_credentials``), not as a fixed companion-agent set, so
# they are not modeled here.


# --- LLM provider credential groups -----------------------------------------


class OpenAISettings(BaseSettings):
    """``OPENAI_*`` credentials."""

    model_config = SettingsConfigDict(
        env_prefix="OPENAI_", case_sensitive=False, extra="ignore"
    )

    api_key: str = ""


class AnthropicSettings(BaseSettings):
    """``ANTHROPIC_*`` credentials."""

    model_config = SettingsConfigDict(
        env_prefix="ANTHROPIC_", case_sensitive=False, extra="ignore"
    )

    api_key: str = ""


class GoogleSettings(BaseSettings):
    """Google / Gemini credentials (Developer API key or Vertex AI)."""

    model_config = SettingsConfigDict(
        env_prefix="GOOGLE_", case_sensitive=False, extra="ignore"
    )

    api_key: str = ""  # GOOGLE_API_KEY
    gemini_api_key: str = Field("", validation_alias="GEMINI_API_KEY")
    genai_use_vertexai: bool = False  # GOOGLE_GENAI_USE_VERTEXAI
    cloud_project: str = ""  # GOOGLE_CLOUD_PROJECT

    @property
    def has_credentials(self) -> bool:
        """Whether the Gemini Developer API or Vertex AI is configured."""
        if self.api_key or self.gemini_api_key:
            return True
        return self.genai_use_vertexai and bool(self.cloud_project)


# --- Adapter runtime configuration groups -----------------------------------
#
# Self-contained: each group owns all of its own config, including bits whose
# environment variable does not share the group prefix (declared via
# ``validation_alias``). Factories instantiate these directly rather than
# carrying them on the shared E2ESettings base.


class CodexSettings(BaseSettings):
    """``CODEX_*`` runtime config for the Codex adapter E2E factory."""

    model_config = SettingsConfigDict(
        env_prefix="CODEX_", case_sensitive=False, extra="ignore"
    )

    transport: str = "stdio"
    command: str = ""
    ws_url: str = "ws://127.0.0.1:8765"
    model: str = ""
    approval_policy: str = "never"
    approval_mode: str = "manual"
    cwd: str = ""
    cwd_is_disposable: bool = Field(
        False, validation_alias="E2E_CODEX_CWD_IS_DISPOSABLE"
    )
    allow_write_capable_auto_approval: bool = Field(
        False, validation_alias="E2E_ALLOW_WRITE_CAPABLE_AUTO_APPROVAL"
    )


class OpencodeSettings(BaseSettings):
    """``OPENCODE_*`` runtime config for the OpenCode adapter E2E factory."""

    model_config = SettingsConfigDict(
        env_prefix="OPENCODE_", case_sensitive=False, extra="ignore"
    )

    base_url: str = ""
    provider_id: str = "opencode"
    model_id: str = "minimax-m2.5-free"
    agent: str = ""
    approval_mode: str = "auto_decline"
    question_mode: str = "auto_reject"
    allow_write_capable_auto_approval: bool = Field(
        False, validation_alias="E2E_ALLOW_WRITE_CAPABLE_AUTO_APPROVAL"
    )


class LettaSettings(BaseSettings):
    """``LETTA_*`` runtime config for the Letta adapter E2E factory."""

    model_config = SettingsConfigDict(
        env_prefix="LETTA_", case_sensitive=False, extra="ignore"
    )

    base_url: str = "https://api.letta.com"
    api_key: str = ""
    project: str = ""
    model: str = "openai/gpt-5.4-mini"
    mcp_server_url: str = Field("", validation_alias="MCP_SERVER_URL")
