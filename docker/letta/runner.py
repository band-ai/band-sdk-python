"""Production runner for Thenvoi Letta agents.

Reads agent configuration from a YAML file and runs the Letta adapter
with retry logic and graceful shutdown support.  Designed for Docker
deployment — all configuration is via environment variables.

Environment variables:
    AGENT_CONFIG               Path to the YAML config file (required)
    AGENT_KEY                  Key to look up in keyed config (default: "agent")
    LETTA_BASE_URL             Letta server URL (default: https://api.letta.com)
                               Set to http://localhost:8283 for self-hosted.
    LETTA_API_KEY              Letta API key (required for Cloud, optional for self-hosted)
    LETTA_MODEL                Model ID (e.g., openai/gpt-4o)
    LETTA_MODE                 Operating mode: per_room or shared (default: per_room)
    LETTA_PROJECT              Letta Cloud project name (optional, ignored for self-hosted)
    MCP_SERVER_URL             thenvoi-mcp server URL (default: http://localhost:8002/sse)
    MCP_SERVER_NAME            MCP server name (default: thenvoi)
    THENVOI_WS_URL             Platform WebSocket URL
    THENVOI_REST_URL           Platform REST URL
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Any, Literal

try:
    import yaml
except ImportError:
    raise ImportError(
        "pyyaml is required for the Letta runner. Install with: pip install pyyaml"
    )

from thenvoi.config.loader import load_agent_config

# Global flag for graceful shutdown
_shutdown_event: asyncio.Event | None = None

# Retry configuration for connection failures
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 60.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

LettaMode = Literal["per_room", "shared"]

_MODES: dict[str, LettaMode] = {"per_room": "per_room", "shared": "shared"}


def load_config(config_path: str, agent_key: str) -> dict[str, Any]:
    """Load agent configuration from YAML file."""
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


def _optional_str(value: Any) -> str | None:
    """Return a stripped string or None for empty/missing values."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_mode(value: str) -> LettaMode:
    parsed = _MODES.get(value.lower())
    if parsed is None:
        raise ValueError(
            f"LETTA_MODE must be one of {', '.join(sorted(_MODES))}; got: {value}"
        )
    return parsed


def _handle_signal(sig: signal.Signals) -> None:
    """Handle shutdown signals (SIGTERM, SIGINT)."""
    logger.info("Received %s, initiating graceful shutdown...", sig.name)
    if _shutdown_event:
        _shutdown_event.set()


async def main() -> None:
    """Run the Letta agent from YAML configuration."""
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
        "THENVOI_WS_URL", "wss://app.band.ai/api/v1/socket/websocket"
    )
    rest_url = os.environ.get("THENVOI_REST_URL", "https://app.band.ai")
    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is empty")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is empty")

    logger.info("Loading config from: %s (key: %s)", config_path, agent_key)
    config = load_config(config_path, agent_key)

    from thenvoi import Agent
    from thenvoi.adapters.letta import LettaAdapter, LettaAdapterConfig

    agent_id = config["agent_id"]
    api_key = config["api_key"]

    # Letta-specific config from environment (env overrides YAML)
    letta_base_url = (
        _optional_str(os.environ.get("LETTA_BASE_URL"))
        or _optional_str(config.get("letta_base_url"))
        or "https://api.letta.com"
    )
    letta_api_key = _optional_str(os.environ.get("LETTA_API_KEY")) or _optional_str(
        config.get("letta_api_key")
    )
    letta_model = _optional_str(os.environ.get("LETTA_MODEL")) or _optional_str(
        config.get("letta_model")
    )
    letta_mode = _parse_mode(
        _optional_str(os.environ.get("LETTA_MODE"))
        or _optional_str(config.get("letta_mode"))
        or "per_room"
    )
    mcp_server_url = (
        _optional_str(os.environ.get("MCP_SERVER_URL"))
        or _optional_str(config.get("mcp_server_url"))
        or "http://localhost:8002/sse"
    )
    mcp_server_name = (
        _optional_str(os.environ.get("MCP_SERVER_NAME"))
        or _optional_str(config.get("mcp_server_name"))
        or "thenvoi"
    )
    letta_project = _optional_str(os.environ.get("LETTA_PROJECT")) or _optional_str(
        config.get("letta_project")
    )

    adapter = LettaAdapter(
        config=LettaAdapterConfig(
            base_url=letta_base_url,
            api_key=letta_api_key,
            model=letta_model,
            mode=letta_mode,
            mcp_server_url=mcp_server_url,
            mcp_server_name=mcp_server_name,
            project=letta_project,
            custom_section="",
            include_base_instructions=True,
            enable_task_events=True,
            enable_execution_reporting=False,
        )
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Letta agent: %s", agent_id)
    logger.info(
        "Letta config: base_url=%s, mode=%s, model=%s, mcp=%s, project=%s",
        letta_base_url,
        letta_mode,
        letta_model or "auto",
        mcp_server_url,
        letta_project or "(none)",
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
