"""Configuration for the OpenCode adapter."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ApprovalMode = Literal["manual", "auto_accept", "auto_decline"]
QuestionMode = Literal["manual", "auto_reject"]
ApprovalReply = Literal["once", "always", "reject"]


class OpencodeAdapterConfig(BaseSettings):
    """Runtime configuration for OpenCode sessions.

    Every field can be set explicitly (highest priority) or via an
    ``OPENCODE_``-prefixed environment variable (e.g. ``OPENCODE_BASE_URL``,
    ``OPENCODE_PROVIDER_ID``). An explicit constructor kwarg always wins
    over the environment.
    """

    # extra="forbid" (not the usual settings "ignore"): this config is
    # commonly built with many explicit kwargs, so a typo'd field name
    # must fail construction instead of silently vanishing.
    model_config = SettingsConfigDict(
        env_prefix="OPENCODE_",
        case_sensitive=False,
        extra="forbid",
        env_ignore_empty=True,
    )

    base_url: str = "http://127.0.0.1:4096"
    directory: str | None = None
    workspace: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    agent: str | None = None
    variant: str | None = None
    custom_section: str = ""
    include_base_instructions: bool = False
    fallback_send_agent_text: bool = True
    turn_timeout_s: float = 300.0
    approval_mode: ApprovalMode = "manual"
    approval_wait_timeout_s: float = 300.0
    approval_timeout_reply: ApprovalReply = "reject"
    question_mode: QuestionMode = "manual"
    question_wait_timeout_s: float = 300.0
    session_title_prefix: str = "Band"
    mcp_server_name: str = "band"
