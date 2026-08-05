"""
Agent configuration utilities.

Usage:
    from band.config import load_agent_config, LogSettings

    agent_id, api_key = load_agent_config("my_agent")
    LogSettings().configure()
"""

from band.config.loader import load_agent_config, get_config_path
from band.config.logs import LogSettings, configure_logging_from_env

__all__ = [
    "LogSettings",
    "configure_logging_from_env",
    "get_config_path",
    "load_agent_config",
]
