"""
Agent configuration management utilities.

This module provides functions to load agent credentials from a YAML
configuration file. Agents must be created manually on the platform
as remote agents before use.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def get_config_path() -> Path:
    """
    Get the path to the agent configuration file.

    Looks for agent_config.yaml in the current working directory (project root).
    """
    return Path(os.getcwd()) / "agent_config.yaml"


def load_agent_config(
    agent_key: str,
    *,
    config_path: str | Path | None = None,
) -> tuple[str, str]:
    """
    Load agent credentials from a YAML configuration file.

    Supports two formats:

    1. **Keyed format** (``agent_config.yaml``)::

           planner:
             agent_id: "..."
             api_key: "..."

       Looked up via *agent_key*.

    2. **Flat format** (Docker runner YAML)::

           agent_id: "..."
           api_key: "..."
           role: planner

       When *agent_key* is not found as a top-level key **and**
       ``agent_id`` exists at the top level, the file is treated as
       a flat single-agent config.

    Args:
        agent_key: The key identifying the agent in the config file.
        config_path: Explicit path to the YAML file.  When *None*,
            falls back to ``agent_config.yaml`` in the working directory.

    Returns:
        Tuple of (agent_id, api_key).

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        ValueError: If required fields (agent_id, api_key) are missing or empty.
    """
    path = Path(config_path) if config_path is not None else get_config_path()
    logger.debug("Loading config from: %s", path)

    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found at {path}. "
            "Copy agent_config.yaml.example to agent_config.yaml and configure your agents."
        )

    try:
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        # Try keyed lookup first
        agent_config = config.get(agent_key, {})

        # Fall back to flat format (Docker runner YAMLs put agent_id at top level)
        if not agent_config and "agent_id" in config:
            agent_config = config

        if not agent_config:
            raise ValueError(
                f"Agent '{agent_key}' not found in {path}. "
                "Please add the agent configuration."
            )

        # Require agent_id and api_key
        agent_id = agent_config.get("agent_id")
        api_key = agent_config.get("api_key")

        missing_fields: list[str] = []
        if not agent_id:
            missing_fields.append("agent_id")
        if not api_key:
            missing_fields.append("api_key")

        if missing_fields:
            raise ValueError(
                f"Missing required fields for agent '{agent_key}': {', '.join(missing_fields)}. "
                f"Please create a remote agent on the platform and add the credentials to {path}"
            )

        return agent_id, api_key
    except (ValueError, FileNotFoundError):
        raise
    except Exception as e:
        raise RuntimeError(f"Error loading agent config: {e}") from e
