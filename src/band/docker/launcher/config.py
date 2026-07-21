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

from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from band.docker.launcher.errors import LaunchError
from band.docker.repo_init import is_https_url, is_ssh_url

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
    """Optional repository bootstrap: the project is cloned into the fenced
    project path before dependency sync instead of arriving with the mount."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    url: str
    branch: str | None = None
    index: bool = False

    @field_validator("url")
    @classmethod
    def url_must_be_supported(cls, value: str) -> str:
        """A structurally complete SSH/HTTPS remote with no embedded secrets.

        A blank URL must not slip through: repo_init normalizes it to None
        and would silently fall into its URL-less local-only mode. Host and
        path must parse so a malformed remote fails here, not at git time.
        Userinfo tokens are rejected outright — band.yaml is a committed
        file, and repo_init logs the URL and embeds it in git error text.
        """
        cleaned = value.strip()
        if not (is_ssh_url(cleaned) or is_https_url(cleaned)):
            raise ValueError(
                "must be an SSH (git@… / ssh://…) or https:// repository URL"
            )
        if cleaned.startswith("git@"):
            host, sep, path = cleaned.removeprefix("git@").partition(":")
            if not (host and sep and path):
                raise ValueError("an SCP-form SSH remote must look like git@host:path")
            return cleaned
        parts = urlsplit(cleaned)
        _ = parts.port  # force the lazy port parse
        if not parts.hostname or len(parts.path) <= 1:
            raise ValueError("must include a host and a repository path")
        # ssh://git@host/… is the canonical SSH login user, not a secret;
        # everything else in userinfo is a credential and never belongs in
        # band.yaml — use the environment or the opt-in credential file.
        if parts.password or (parts.scheme == "https" and parts.username):
            raise ValueError(
                "must not embed credentials; use the environment or the "
                "opt-in credential file"
            )
        return cleaned


class CredentialSource(StrEnum):
    """The custody modes ``band.yaml`` may select for credentials.

    ``workspace-env-file`` keeps plaintext keys in the workspace and the VM;
    ``proxy-managed`` keeps only sentinels in the VM while a trusted host-side
    proxy injects the real keys on the wire.

    ``PROXY_MANAGED`` here is the custody-mode *name*, distinct from the sentinel
    api-key *value* ``band.credentials.PROXY_MANAGED_API_KEY`` — same spelling,
    different concept.
    """

    WORKSPACE_ENV_FILE = "workspace-env-file"
    PROXY_MANAGED = "proxy-managed"


class CredentialsSection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: CredentialSource
    # Required for ``workspace-env-file`` (the file to read); unused for
    # ``proxy-managed`` (there is no file), enforced below.
    path: str | None = None
    acknowledge_plaintext_in_sandbox: bool = Field(
        default=False, alias="acknowledgePlaintextInSandbox"
    )

    @model_validator(mode="after")
    def path_matches_source(self) -> CredentialsSection:
        if self.source is CredentialSource.WORKSPACE_ENV_FILE and not self.path:
            raise ValueError(f"path is required for source '{self.source}'")
        if self.source is CredentialSource.PROXY_MANAGED and self.path is not None:
            raise ValueError(f"path is not used with source '{self.source}'")
        return self


class RuntimeSection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    environment_path: str = Field(alias="environmentPath")
    state_path: str = Field(alias="statePath")
    cache_path: str = Field(alias="cachePath")
    log_path: str = Field(alias="logPath")


class WorkspaceConfig(BaseModel):
    """The customer's ``band.yaml``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # The one schema version this launcher implements. A file declaring any
    # other version was written for different semantics and must not be
    # interpreted with this model.
    schema_version: Literal["1"] = Field(alias="schemaVersion")
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

    # The one path override: where to find band.yaml. Every other path is
    # workspace configuration — band.yaml owns it.
    band_kit_config_path: str = ""

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


def require_url(value: str, *, scheme: str, name: str) -> str:
    """A structurally valid endpoint: the right scheme AND a hostname, so a
    bad URL fails this phase instead of the first connect after the sync."""
    try:
        parts = urlsplit(value)
        # netloc is truthy for host-less forms like "https://:443" and
        # "wss://user@" — only hostname proves a host. Reading port forces
        # its lazy parse so "https://host:not-a-port" fails here too.
        hostname, _ = parts.hostname, parts.port
    except ValueError as exc:
        raise LaunchError("config", f"{name} is not a valid URL: {value!r}") from exc
    if parts.scheme != scheme or not hostname:
        raise LaunchError(
            "config", f"{name} must be a {scheme}:// URL with a host, got {value!r}"
        )
    return value


def resolve_endpoints(config: WorkspaceConfig, env: LauncherEnv) -> tuple[str, str]:
    """Resolve REST/WS URLs: env override → band.yaml → production defaults."""
    rest = env.band_rest_url or config.band.rest_url or DEFAULT_REST_URL
    ws = env.band_ws_url or config.band.ws_url or DEFAULT_WS_URL
    return (
        require_url(rest, scheme="https", name="BAND_REST_URL"),
        require_url(ws, scheme="wss", name="BAND_WS_URL"),
    )


def resolve_agent_id(config: WorkspaceConfig, env: LauncherEnv) -> str:
    agent_id = env.band_agent_id or config.agent.id
    if not agent_id:
        raise LaunchError(
            "config",
            "agent id missing: set agent.id in band.yaml or BAND_AGENT_ID",
        )
    return agent_id
