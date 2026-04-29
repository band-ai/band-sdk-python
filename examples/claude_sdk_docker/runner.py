#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[claude_sdk]", "pyyaml"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
YAML-based agent runner for Thenvoi Claude SDK.

Reads agent configuration from a YAML file and runs the agent.
Designed for Docker deployment without writing Python code.

Usage:
    AGENT_CONFIG=/app/config/agent.yaml python runner.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import signal
from pathlib import Path
from typing import Any

import yaml

from thenvoi.config.loader import load_agent_config
from thenvoi.core.types import AdapterFeatures, Emit

# Global flag for graceful shutdown
_shutdown_event: asyncio.Event | None = None

# Required mount points per SRS NFR-007
REQUIRED_MOUNTS = [
    "/workspace/repo",
    "/workspace/notes",
    "/workspace/state",
]

# Retry configuration for connection failures
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 1.0  # seconds
MAX_RETRY_DELAY = 60.0  # seconds

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def validate_mounts() -> None:
    """Validate required mount points exist (NFR-007a).

    Raises ValueError with actionable message if any required mount is missing.
    """
    missing = [m for m in REQUIRED_MOUNTS if not Path(m).is_dir()]
    if missing:
        raise ValueError(
            f"Missing required mount points: {missing}. "
            "Ensure docker-compose.yml mounts: "
            f"{', '.join(f'{m} (rw)' for m in REQUIRED_MOUNTS)}. "
            "See README.md for mount contract details."
        )


def load_config(config_path: str) -> dict[str, Any]:
    """Load agent configuration from YAML file.

    Credentials (agent_id, api_key) are validated via the SDK's
    ``load_agent_config()`` so all examples follow a single path
    for credential loading.  Additional fields (role, model, prompt,
    etc.) are returned as-is for the runner to consume.
    """
    path = Path(config_path).resolve()
    if not path.exists():
        raise ValueError(f"Config file not found: {config_path}")

    try:
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in config file: {e}") from e
    except OSError as e:
        raise ValueError(f"Failed to read config file: {e}") from e

    if config is None:
        raise ValueError("Config file is empty")

    # Validate credentials via the SDK config loader (supports flat YAML format)
    agent_id, api_key = load_agent_config("agent", config_path=path)
    config["agent_id"] = agent_id
    config["api_key"] = api_key

    return config


def load_custom_tools(tools_dir: Path, config_dir: Path, tool_names: list[str]) -> list:
    """Load custom tools from tools directory.

    Args:
        tools_dir: Path to the tools directory.
        config_dir: Path to the config directory (for path traversal validation).
        tool_names: List of tool names to load.

    Returns a list of tool functions (decorated with @tool from claude_agent_sdk).
    """
    # Resolve and validate path to prevent path traversal
    resolved_tools_dir = tools_dir.resolve()
    resolved_config_dir = config_dir.resolve()

    # Ensure tools_dir is within or a sibling of config_dir
    try:
        resolved_tools_dir.relative_to(resolved_config_dir.parent)
    except ValueError:
        logger.warning(
            "Tools directory %s is outside allowed path, skipping",
            resolved_tools_dir,
        )
        return []

    tools_init = resolved_tools_dir / "__init__.py"
    if not tools_init.exists():
        return []

    # Use importlib to load module without modifying sys.path
    try:
        spec = importlib.util.spec_from_file_location("tools", tools_init)
        if spec is None or spec.loader is None:
            logger.warning("Could not create module spec for tools")
            return []

        tools_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tools_module)

        tool_registry = getattr(tools_module, "TOOL_REGISTRY", {})
        # Filter to only requested tools, return as list
        return [tool_registry[name] for name in tool_names if name in tool_registry]
    except Exception as e:
        logger.warning("Could not load custom tools: %s", e)
        return []


def _handle_signal(sig: signal.Signals) -> None:
    """Handle shutdown signals (SIGTERM, SIGINT)."""
    logger.info("Received %s, initiating graceful shutdown...", sig.name)
    if _shutdown_event:
        _shutdown_event.set()


async def main() -> None:
    """Run the agent from YAML configuration."""
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    # Setup signal handlers for graceful shutdown (Docker sends SIGTERM)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    # Get config path from environment
    config_path = os.environ.get("AGENT_CONFIG")
    if not config_path:
        raise ValueError("AGENT_CONFIG environment variable not set")

    # Validate Thenvoi platform URLs
    ws_url = os.environ.get(
        "THENVOI_WS_URL", "wss://app.thenvoi.com/api/v1/socket/websocket"
    )
    rest_url = os.environ.get("THENVOI_REST_URL", "https://app.thenvoi.com")
    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is empty")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is empty")

    # Validate required mount points (NFR-007a)
    validate_mounts()

    logger.info("Loading config from: %s", config_path)
    config = load_config(config_path)

    # Import here to allow early config validation
    from thenvoi import Agent
    from thenvoi.adapters import ClaudeSDKAdapter

    # Extract config values
    agent_id = config["agent_id"]
    api_key = config["api_key"]
    model = config.get("model")
    fallback_model = config.get("fallback_model")
    custom_prompt = config.get("prompt", "")
    thinking_tokens = config.get("thinking_tokens")
    tool_names = config.get("tools", [])

    # Working directory for Claude Code (env overrides config)
    workspace = os.environ.get("WORKSPACE") or config.get("workspace")

    # Get role from config or environment (env overrides config)
    role = os.environ.get("AGENT_ROLE") or config.get("role")

    # Build final prompt combining role and custom prompt
    config_dir = Path(config_path).parent
    prompt_dir = config_dir / "prompts"
    final_prompt_parts: list[str] = []

    if role:
        prompt_file = prompt_dir / f"{role}.md"
        if prompt_file.exists():
            final_prompt_parts.append(prompt_file.read_text(encoding="utf-8"))
            logger.info("Using role prompt from: %s", prompt_file)
        else:
            logger.warning(
                "Role '%s' specified but no prompt file at %s", role, prompt_file
            )

    if custom_prompt:
        final_prompt_parts.append(custom_prompt)

    # Default prompt if nothing specified
    if not final_prompt_parts:
        final_prompt_parts.append("You are a helpful assistant.")

    final_prompt = "\n\n".join(final_prompt_parts)

    # Load custom tools if specified
    custom_tools = []
    if tool_names:
        tools_dir = config_dir / "tools"
        custom_tools = load_custom_tools(tools_dir, config_dir, tool_names)
        if custom_tools:
            tool_fn_names = [getattr(t, "_tool_name", t.__name__) for t in custom_tools]
            logger.info("Loaded custom tools: %s", tool_fn_names)

    # Create adapter
    adapter = ClaudeSDKAdapter(
        model=model,
        fallback_model=fallback_model,
        custom_section=final_prompt,
        max_thinking_tokens=thinking_tokens,
        features=AdapterFeatures(emit={Emit.EXECUTION, Emit.THOUGHTS}),
        additional_tools=custom_tools if custom_tools else None,
        cwd=workspace,
    )

    # Create agent
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting agent: %s", agent_id)
    logger.info("Model: %s", model or "auto")
    if fallback_model:
        logger.info("Fallback model: %s", fallback_model)
    if role:
        logger.info("Role: %s", role)
    if workspace:
        logger.info("Workspace: %s", workspace)
    if thinking_tokens:
        logger.info("Extended thinking: %s tokens", thinking_tokens)
    logger.info("Press Ctrl+C to stop")

    retry_count = 0
    retry_delay = INITIAL_RETRY_DELAY

    while not _shutdown_event.is_set():
        try:
            # Run agent with shutdown event monitoring
            agent_task = asyncio.create_task(agent.run())
            shutdown_task = asyncio.create_task(_shutdown_event.wait())

            # Wait for either agent completion or shutdown signal
            done, pending = await asyncio.wait(
                [agent_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Check if agent task raised an exception
            if agent_task in done:
                agent_task.result()

            # Reset retry state on successful connection
            retry_count = 0
            retry_delay = INITIAL_RETRY_DELAY

            # If we get here without exception, agent completed normally
            break

        except (ConnectionError, OSError) as e:
            retry_count += 1
            if retry_count > MAX_RETRIES:
                logger.error("Max retries (%s) exceeded, giving up", MAX_RETRIES)
                raise

            logger.warning(
                "Connection error: %s. Retrying in %.1fs (attempt %s/%s)",
                e,
                retry_delay,
                retry_count,
                MAX_RETRIES,
            )
            await asyncio.sleep(retry_delay)
            # Exponential backoff with cap
            retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)

        except asyncio.CancelledError:
            logger.info("Agent task cancelled")
            break

    logger.info("Shutting down...")
    try:
        if hasattr(agent, "close"):
            await agent.close()
    except Exception as e:
        logger.warning("Error during agent cleanup: %s", e)
    logger.info("Agent stopped")


if __name__ == "__main__":
    asyncio.run(main())
