"""Production runner for Thenvoi Claude SDK agents.

Reads agent configuration from a YAML file and runs the agent with
retry logic and graceful shutdown support.  Designed for Docker
deployment — all configuration is via environment variables.

Environment variables:
    AGENT_CONFIG   Path to the YAML config file (required)
    AGENT_KEY      Key to look up in keyed config (default: "agent")
    AGENT_ROLE     Role override (planner, reviewer)
    WORKSPACE      Working directory override
    THENVOI_WS_URL     Platform WebSocket URL
    THENVOI_REST_URL   Platform REST URL
    ANTHROPIC_API_KEY  Anthropic API key
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

from repo_init import initialize_repo
from thenvoi.config.loader import load_agent_config

# Global flag for graceful shutdown
_shutdown_event: asyncio.Event | None = None

# Required mount points
REQUIRED_MOUNTS = [
    "/workspace/repo",
    "/workspace/notes",
    "/workspace/state",
    "/workspace/context",
]

# Retry configuration for connection failures
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 60.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def validate_mounts() -> None:
    """Validate required mount points exist."""
    missing = [m for m in REQUIRED_MOUNTS if not Path(m).is_dir()]
    if missing:
        raise ValueError(
            f"Missing required mount points: {missing}. "
            "Ensure docker-compose.yml mounts: "
            f"{', '.join(f'{m} (rw)' for m in REQUIRED_MOUNTS)}."
        )


def load_config(config_path: str, agent_key: str) -> dict[str, Any]:
    """Load agent configuration from YAML file.

    Credentials (agent_id, api_key) are validated via the SDK's
    ``load_agent_config()``.  Additional fields (role, model, prompt,
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

    agent_id, api_key = load_agent_config(agent_key, config_path=path)

    agent_section = config.get(agent_key, {})
    result = dict(agent_section) if agent_section else dict(config)
    result["agent_id"] = agent_id
    result["api_key"] = api_key

    return result


def load_custom_tools(tools_dir: Path, config_dir: Path, tool_names: list[str]) -> list:
    """Load custom tools from tools directory."""
    resolved_tools_dir = tools_dir.resolve()
    resolved_config_dir = config_dir.resolve()

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

    try:
        spec = importlib.util.spec_from_file_location("tools", tools_init)
        if spec is None or spec.loader is None:
            logger.warning("Could not create module spec for tools")
            return []

        tools_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tools_module)

        tool_registry = getattr(tools_module, "TOOL_REGISTRY", {})
        return [tool_registry[name] for name in tool_names if name in tool_registry]
    except Exception:
        logger.exception("Could not load custom tools")
        return []


def _handle_signal(sig: signal.Signals) -> None:
    """Handle shutdown signals (SIGTERM, SIGINT)."""
    logger.info("Received %s, initiating graceful shutdown...", sig.name)
    if _shutdown_event:
        _shutdown_event.set()


async def main() -> None:
    """Run the agent from YAML configuration."""
    global _shutdown_event  # noqa: PLW0603 — module-level event for signal handlers
    _shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    config_path = os.environ.get("AGENT_CONFIG")
    if not config_path:
        raise ValueError("AGENT_CONFIG environment variable not set")

    agent_key = os.environ.get("AGENT_KEY", "agent")

    ws_url = os.environ.get(
        "THENVOI_WS_URL", "wss://app.thenvoi.com/api/v1/socket/websocket"
    )
    rest_url = os.environ.get("THENVOI_REST_URL", "https://app.thenvoi.com")
    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is empty")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is empty")

    validate_mounts()

    logger.info("Loading config from: %s (key: %s)", config_path, agent_key)
    config = load_config(config_path, agent_key)
    lock_timeout_s = float(os.environ.get("REPO_INIT_LOCK_TIMEOUT_S", "120"))
    repo_init = initialize_repo(
        config,
        agent_key=agent_key,
        lock_timeout_s=lock_timeout_s,
    )

    from thenvoi import Agent
    from thenvoi.adapters import ClaudeSDKAdapter

    agent_id = config["agent_id"]
    api_key = config["api_key"]
    model = config.get("model")
    fallback_model = config.get("fallback_model")
    custom_prompt = config.get("prompt", "")
    thinking_tokens = config.get("thinking_tokens")
    tool_names = config.get("tools", [])
    workspace = (
        os.environ.get("WORKSPACE") or config.get("workspace") or repo_init.repo_path
    )
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

    if repo_init.context_bundle:
        final_prompt_parts.append(repo_init.context_bundle)

    if not final_prompt_parts:
        final_prompt_parts.append("You are a helpful assistant.")

    final_prompt = "\n\n".join(final_prompt_parts)

    # Load custom tools if specified
    custom_tools: list = []
    if tool_names:
        tools_dir = config_dir / "tools"
        custom_tools = load_custom_tools(tools_dir, config_dir, tool_names)
        if custom_tools:
            tool_fn_names = [getattr(t, "_tool_name", t.__name__) for t in custom_tools]
            logger.info("Loaded custom tools: %s", tool_fn_names)

    adapter = ClaudeSDKAdapter(
        model=model,
        fallback_model=fallback_model,
        custom_section=final_prompt,
        max_thinking_tokens=thinking_tokens,
        enable_execution_reporting=True,
        additional_tools=custom_tools if custom_tools else None,
        cwd=workspace,
    )

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
    if repo_init.enabled:
        logger.info(
            "Repo init: cloned=%s indexed=%s path=%s",
            repo_init.cloned,
            repo_init.indexed,
            repo_init.repo_path,
        )

    retry_count = 0
    retry_delay = INITIAL_RETRY_DELAY

    while not _shutdown_event.is_set():
        try:
            agent_task = asyncio.create_task(agent.run())
            shutdown_task = asyncio.create_task(_shutdown_event.wait())

            done, pending = await asyncio.wait(
                [agent_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if agent_task in done:
                agent_task.result()

            retry_count = 0
            retry_delay = INITIAL_RETRY_DELAY
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
            retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)

        except asyncio.CancelledError:
            logger.info("Agent task cancelled")
            break

    logger.info("Shutting down...")
    try:
        if hasattr(agent, "close"):
            await agent.close()
    except Exception:
        logger.exception("Error during agent cleanup")
    logger.info("Agent stopped")


if __name__ == "__main__":
    asyncio.run(main())
