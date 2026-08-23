"""
Agent configuration utilities.

Usage:
    from band.config import load_agent_config, LogSettings

    agent_id, api_key = load_agent_config("my_agent")
    LogSettings().configure()
"""

from band.config.loader import load_agent_config, get_config_path
from band.config.logs import LogSettings, configure_logging_from_env
from band.config.settings import (
    DEFAULT_REST_URL,
    DEFAULT_WS_URL,
    PlatformSettings,
)

__all__ = [
    "DEFAULT_REST_URL",
    "DEFAULT_WS_URL",
    "LogSettings",
    "PlatformSettings",
    "configure_logging_from_env",
    "get_config_path",
    "load_agent_config",
]
