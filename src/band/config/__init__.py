"""
Agent configuration utilities.

Usage:
    from band.config import load_agent_config

    agent_id, api_key = load_agent_config("my_agent")
"""

from band.config.loader import load_agent_config, get_config_path

__all__ = ["load_agent_config", "get_config_path"]
