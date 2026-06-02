"""
Framework integrations for Band SDK.

This module contains utilities for integrating with various AI frameworks:
- base: Common helpers for all frameworks
- langgraph: LangChain/LangGraph tools and utilities
- parlant: Parlant SDK session and tool management
- claude_sdk: Claude Agent SDK session management
"""

# Base utilities (always available)
from .base import check_and_format_participants

__all__ = [
    "check_and_format_participants",
]

# Optional imports - fail gracefully if deps not installed
