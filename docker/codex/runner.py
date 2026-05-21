"""Production runner for Thenvoi Codex agents.

Reads agent configuration from a YAML file and runs the Codex adapter
with retry logic and graceful shutdown support.  Designed for Docker
deployment — all configuration is via environment variables.

Environment variables:
    AGENT_CONFIG               Path to the YAML config file (required)
    AGENT_KEY                  Key to look up in keyed config (default: "agent")
    CODEX_CWD                  Working directory for Codex (default: /workspace/repo)
    CODEX_TRANSPORT            Transport mode: stdio or ws (default: stdio)
    CODEX_MODEL                Model ID override
    CODEX_ROLE                 Role name; loads prompt from {config_dir}/prompts/{role}.md
    CODEX_SANDBOX              Sandbox mode (default: external-sandbox)
    CODEX_REASONING_EFFORT     Reasoning effort level
    CODEX_APPROVAL_MODE        Approval mode: manual, auto_accept, auto_decline
    CODEX_TURN_TASK_MARKERS    Emit turn task markers: true/false (default: false)
    THENVOI_WS_URL             Platform WebSocket URL
    THENVOI_REST_URL           Platform REST URL
    OPENAI_API_KEY             OpenAI API key
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Any, Literal

import yaml

from thenvoi.docker.repo_init import initialize_repo
from thenvoi.config.loader import load_agent_config

# Global flag for graceful shutdown
_shutdown_event: asyncio.Event | None = None

# Required mount points
REQUIRED_MOUNTS = [
    "/workspace/repo",
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

CodexTransport = Literal["stdio", "ws"]
CodexApprovalMode = Literal["manual", "auto_accept", "auto_decline"]
CodexReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]

_TRANSPORTS: dict[str, CodexTransport] = {"stdio": "stdio", "ws": "ws"}
_APPROVAL_MODES: dict[str, CodexApprovalMode] = {
    "manual": "manual",
    "auto_accept": "auto_accept",
    "auto_decline": "auto_decline",
}
_REASONING_EFFORTS: dict[str, CodexReasoningEffort] = {
    "none": "none",
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
}


def validate_mounts() -> None:
    """Validate required mount points exist."""
    missing = [m for m in REQUIRED_MOUNTS if not Path(m).is_dir()]
    if missing:
        raise ValueError(
            f"Missing required mount points: {missing}. "
            "Ensure docker-compose.yml mounts /workspace/repo."
        )


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


def _env_bool(key: str, default: bool = False) -> bool:
    """Read a boolean from an environment variable."""
    val = os.environ.get(key, "")
    if not val:
        return default
    return val.lower() in ("1", "true", "yes")


def _optional_str(value: Any) -> str | None:
    """Return a stripped string or None for empty/missing values."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_transport(value: str) -> CodexTransport:
    parsed = _TRANSPORTS.get(value.lower())
    if parsed is None:
        raise ValueError(
            f"CODEX_TRANSPORT must be one of {', '.join(sorted(_TRANSPORTS))}; got: {value}"
        )
    return parsed


def _parse_approval_mode(value: str) -> CodexApprovalMode:
    parsed = _APPROVAL_MODES.get(value.lower())
    if parsed is None:
        raise ValueError(
            "CODEX_APPROVAL_MODE must be one of "
            f"{', '.join(sorted(_APPROVAL_MODES))}; got: {value}"
        )
    return parsed


def _parse_reasoning_effort(value: str | None) -> CodexReasoningEffort | None:
    if value is None:
        return None
    parsed = _REASONING_EFFORTS.get(value.lower())
    if parsed is None:
        raise ValueError(
            "CODEX_REASONING_EFFORT must be one of "
            f"{', '.join(sorted(_REASONING_EFFORTS))}; got: {value}"
        )
    return parsed


def _handle_signal(sig: signal.Signals) -> None:
    """Handle shutdown signals (SIGTERM, SIGINT)."""
    logger.info("Received %s, initiating graceful shutdown...", sig.name)
    if _shutdown_event:
        _shutdown_event.set()


async def main() -> None:
    """Run the Codex agent from YAML configuration."""
    global _shutdown_event  # noqa: PLW0603 — module-level event for signal handlers
    _shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    config_path = os.environ.get("AGENT_CONFIG")
    if not config_path:
        raise ValueError("AGENT_CONFIG environment variable not set")

    agent_key = os.environ.get("AGENT_KEY", os.environ.get("CODEX_AGENT_KEY", "agent"))

    ws_url = os.environ.get(
        "THENVOI_WS_URL", "wss://app.band.ai/api/v1/socket/websocket"
    )
    rest_url = os.environ.get("THENVOI_REST_URL", "https://app.band.ai")
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
    from thenvoi.adapters import CodexAdapter
    from thenvoi.adapters.codex import CodexAdapterConfig

    agent_id = config["agent_id"]
    api_key = config["api_key"]

    # Codex-specific config from environment (env overrides YAML)
    codex_cwd = (
        _optional_str(os.environ.get("CODEX_CWD"))
        or _optional_str(config.get("workspace"))
        or repo_init.repo_path
        or "/workspace/repo"
    )
    codex_transport = _parse_transport(
        _optional_str(os.environ.get("CODEX_TRANSPORT")) or "stdio"
    )
    codex_model = _optional_str(os.environ.get("CODEX_MODEL")) or _optional_str(
        config.get("model")
    )
    codex_role = _optional_str(os.environ.get("CODEX_ROLE")) or _optional_str(
        config.get("role")
    )

    # Load role prompt from file if specified
    config_dir = Path(config_path).parent
    prompt_dir = config_dir / "prompts"
    custom_section = ""
    if codex_role:
        prompt_file = prompt_dir / f"{codex_role}.md"
        if prompt_file.exists():
            custom_section = prompt_file.read_text(encoding="utf-8")
            logger.info("Using role prompt from: %s", prompt_file)
        else:
            logger.warning(
                "Role '%s' specified but no prompt file at %s", codex_role, prompt_file
            )

    codex_sandbox = (
        _optional_str(os.environ.get("CODEX_SANDBOX"))
        or _optional_str(config.get("sandbox"))
        or "external-sandbox"
    )
    codex_reasoning = _parse_reasoning_effort(
        _optional_str(os.environ.get("CODEX_REASONING_EFFORT"))
        or _optional_str(config.get("reasoning_effort"))
    )
    codex_approval = _parse_approval_mode(
        _optional_str(os.environ.get("CODEX_APPROVAL_MODE"))
        or _optional_str(config.get("approval_mode"))
        or "manual"
    )
    codex_turn_markers = _env_bool("CODEX_TURN_TASK_MARKERS", default=False)

    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            transport=codex_transport,
            cwd=codex_cwd,
            model=codex_model,
            personality="pragmatic",
            approval_policy="never",
            approval_mode=codex_approval,
            sandbox=codex_sandbox,
            reasoning_effort=codex_reasoning,
            codex_ws_url=os.environ.get("CODEX_WS_URL", "ws://127.0.0.1:8765"),
            custom_section="\n\n".join(
                part for part in (custom_section, repo_init.context_bundle) if part
            ),
            include_base_instructions=True,
            enable_task_events=True,
            emit_turn_task_markers=codex_turn_markers,
            enable_execution_reporting=False,
            emit_thought_events=False,
            fallback_send_agent_text=True,
            experimental_api=True,
        )
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Codex agent: %s", agent_id)
    logger.info(
        "Codex config: transport=%s, model=%s, cwd=%s, sandbox=%s, role=%s",
        codex_transport,
        codex_model or "auto",
        codex_cwd,
        codex_sandbox,
        codex_role or "none",
    )
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
