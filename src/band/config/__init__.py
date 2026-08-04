"""
Agent configuration utilities.

Usage:
    from band.config import load_agent_config

    agent_id, api_key = load_agent_config("my_agent")
"""

from band.config.loader import load_agent_config, get_config_path
from band.config.settings import (
    DEFAULT_REST_URL,
    DEFAULT_WS_URL,
    PlatformSettings,
)

__all__ = [
    "DEFAULT_REST_URL",
    "DEFAULT_WS_URL",
    "PlatformSettings",
    "get_config_path",
    "load_agent_config",
]
