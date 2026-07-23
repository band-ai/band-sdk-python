"""Configuration for the OpenCode adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ApprovalMode = Literal["manual", "auto_accept", "auto_decline"]
QuestionMode = Literal["manual", "auto_reject"]
ApprovalReply = Literal["once", "always", "reject"]


@dataclass
class OpencodeAdapterConfig:
    """Runtime configuration for OpenCode sessions."""

    base_url: str = "http://127.0.0.1:4096"
    directory: str | None = None
    workspace: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    agent: str | None = None
    variant: str | None = None
    custom_section: str = ""
    include_base_instructions: bool = False
    enable_task_events: bool = True
    enable_execution_reporting: bool = False
    enable_memory_tools: bool = False
    fallback_send_agent_text: bool = True
    turn_timeout_s: float = 300.0
    approval_mode: ApprovalMode = "manual"
    approval_wait_timeout_s: float = 300.0
    approval_timeout_reply: ApprovalReply = "reject"
    question_mode: QuestionMode = "manual"
    question_wait_timeout_s: float = 300.0
    session_title_prefix: str = "Band"
    mcp_server_name: str = "band"
