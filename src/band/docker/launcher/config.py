"""Workspace configuration: `band.yaml` (strict) and environment overrides.

`band.yaml` is the customer's declaration of who the agent is and where
things live; every model forbids unknown fields so a typo fails the launch
instead of being silently ignored. `LauncherEnv` is the *only* supported
process-environment surface — a documented set of override variables plus
the values the sandbox runtime and image provide.

Precedence for every value: environment override → `band.yaml` → documented
production defaults (endpoints only) → fail.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from band.docker.launcher.errors import LaunchError

DEFAULT_CONFIG_FILENAME = "band.yaml"
DEFAULT_REST_URL = "https://app.band.ai"
DEFAULT_WS_URL = "wss://app.band.ai/api/v1/socket/websocket"
DEFAULT_SDK_HOME = "/opt/band"
# The startup chain inherits root's HOME across the privilege drop; every
# process the launcher starts must see the agent user's home instead.
AGENT_HOME = "/home/agent"


class AgentSection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = ""
    entrypoint: str = "main.py"


class BandSection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    rest_url: str = Field(default="", alias="restUrl")
    ws_url: str = Field(default="", alias="wsUrl")


class ProjectSection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str = "."


class RepoSection(BaseModel):
    """Mirrors ``band.docker.repo_init.RepoConfig``'s accepted fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str
    url: str | None = None
    branch: str | None = None
    index: bool = False


class CredentialsSection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: str
    path: str
    acknowledge_plaintext_in_sandbox: bool = Field(
        default=False, alias="acknowledgePlaintextInSandbox"
    )


class RuntimeSection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    environment_path: str = Field(alias="environmentPath")
    state_path: str = Field(alias="statePath")
    cache_path: str = Field(alias="cachePath")
    log_path: str = Field(alias="logPath")


class WorkspaceConfig(BaseModel):
    """The customer's ``band.yaml``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = Field(alias="schemaVersion")
    agent: AgentSection = AgentSection()
    band: BandSection = BandSection()
    project: ProjectSection = ProjectSection()
    repo: RepoSection | None = None
    credentials: CredentialsSection | None = None
    runtime: RuntimeSection


class LauncherEnv(BaseSettings):
    """Supported environment overrides plus runtime-provided values.

    Field name == env var name (case-insensitive); everything else in the
    process environment is ignored here and passed through to the customer
    process untouched.
    """

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # Identity / endpoints / credentials overrides.
    band_agent_id: str = ""
    band_api_key: str = ""
    band_rest_url: str = ""
    band_ws_url: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    copilot_github_token: str = ""
    gh_token: str = ""
    github_token: str = ""

    # Path overrides.
    band_kit_config_path: str = ""
    band_kit_credentials_path: str = ""
    band_kit_project_path: str = ""
    band_kit_entrypoint_path: str = ""
    band_kit_repository_path: str = ""
    band_kit_environment_path: str = ""
    band_kit_state_path: str = ""
    band_kit_cache_path: str = ""
    band_kit_log_path: str = ""

    # Image / sandbox-runtime contract values.
    workspace_dir: str = ""
    band_sdk_uv: str = ""
    band_sdk_home: str = DEFAULT_SDK_HOME


def load_workspace_config(config_path: Path) -> WorkspaceConfig:
    """Load and strictly validate ``band.yaml``."""
    if not config_path.is_file():
        raise LaunchError("config", f"workspace configuration not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LaunchError("config", f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LaunchError("config", f"{config_path} must contain a YAML mapping")
    try:
        return WorkspaceConfig.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        raise LaunchError("config", f"invalid {config_path}: {details}") from exc


def resolve_endpoints(config: WorkspaceConfig, env: LauncherEnv) -> tuple[str, str]:
    """Resolve REST/WS URLs: env override → band.yaml → production defaults."""
    rest = env.band_rest_url or config.band.rest_url or DEFAULT_REST_URL
    ws = env.band_ws_url or config.band.ws_url or DEFAULT_WS_URL
    if not rest.startswith("https://"):
        raise LaunchError("config", f"BAND_REST_URL must be https://, got {rest!r}")
    if not ws.startswith("wss://"):
        raise LaunchError("config", f"BAND_WS_URL must be wss://, got {ws!r}")
    return rest, ws


def resolve_agent_id(config: WorkspaceConfig, env: LauncherEnv) -> str:
    agent_id = env.band_agent_id or config.agent.id
    if not agent_id:
        raise LaunchError(
            "config",
            "agent id missing: set agent.id in band.yaml or BAND_AGENT_ID",
        )
    return agent_id
