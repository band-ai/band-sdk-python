"""CLI entry point for band-acp server.

Flag parsing uses `Python Fire <https://github.com/google/python-fire>`_;
validation and env merge stay in :class:`~band.integrations.acp.settings.AcpCliConfig`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import fire

from band.config.logs import LogSettings
from band.integrations.acp.settings import AcpCliConfig
from band.logging_config import LogStream

logger = logging.getLogger(__name__)


def load_config(
    *,
    agent_id: str | None = None,
    api_key: str | None = None,
    rest_url: str | None = None,
    ws_url: str | None = None,
    log_level: str | None = None,
) -> AcpCliConfig:
    """Merge CLI kwargs over ``BAND_*`` env defaults and validate."""
    return AcpCliConfig.from_cli(
        agent_id=agent_id,
        api_key=api_key,
        rest_url=rest_url,
        ws_url=ws_url,
        log_level=log_level,
    )


async def main(
    *,
    agent_id: str | None = None,
    api_key: str | None = None,
    rest_url: str | None = None,
    ws_url: str | None = None,
    log_level: str | None = None,
) -> None:
    """Run the band-acp server with validated config."""
    config = load_config(
        agent_id=agent_id,
        api_key=api_key,
        rest_url=rest_url,
        ws_url=ws_url,
        log_level=log_level,
    )

    # Only an explicit CLI flag overrides BAND_LOG_*; omitted lets env win.
    # The stream is not negotiable: stdout is the JSON-RPC transport, so a log
    # line written there corrupts the editor's ACP session.
    LogSettings.create(
        log_level=config.log_level.value if config.log_level is not None else None,
        log_stream=LogStream.STDERR,
    ).configure()

    # Lazy imports to avoid import errors when ACP deps are not installed
    from band import Agent
    from band.integrations.acp.host import ACPGateway
    from band.integrations.acp.push_handler import ACPPushHandler
    from band.integrations.acp.server import ACPServer
    from band.integrations.acp.server_adapter import BandACPServerAdapter

    adapter = BandACPServerAdapter(
        rest_url=config.rest_url,
        api_key=config.api_key_value,
    )

    push_handler = ACPPushHandler(adapter)
    adapter.set_push_handler(push_handler)

    server = ACPServer(adapter)

    agent = Agent.create(
        adapter=adapter,
        agent_id=config.agent_id,
        api_key=config.api_key_value,
        rest_url=config.rest_url,
        ws_url=config.ws_url,
    )

    logger.info("Starting band-acp server (agent_id=%s)", config.agent_id)

    async with ACPGateway(agent=agent, server=server) as gateway:
        await gateway.serve()


def run(
    agent_id: str | None = None,
    api_key: str | None = None,
    rest_url: str | None = None,
    ws_url: str | None = None,
    log_level: str | None = None,
) -> None:
    """Band ACP server — expose Band peers as an ACP agent.

    Args:
        agent_id: Band agent ID (env: BAND_AGENT_ID).
        api_key: Band API key (env: BAND_API_KEY).
        rest_url: Band REST API URL (env: BAND_REST_URL).
        ws_url: Band WebSocket URL (env: BAND_WS_URL).
        log_level: Logging level DEBUG|INFO|WARNING|ERROR (env: BAND_LOG_LEVEL).
            Omitted so BAND_LOG_* can apply; stdout is never used for logs.
    """
    try:
        asyncio.run(
            main(
                agent_id=agent_id,
                api_key=api_key,
                rest_url=rest_url,
                ws_url=ws_url,
                log_level=log_level,
            )
        )
    except KeyboardInterrupt:
        pass
    except ValueError as exc:
        logger.error("Error: %s", exc)
        raise SystemExit(1) from exc


def entry_point(command: str | list[str] | tuple[str, ...] | None = None) -> Any:
    """CLI entry point for the ``band-acp`` command (Fire-generated flags).

    Returns ``None`` on success so Fire does not write to stdout — that stream
    carries ACP JSON-RPC frames for the editor.
    """
    # ``serialize=lambda _: None`` keeps Fire from printing the return value.
    return fire.Fire(run, name="band-acp", command=command, serialize=lambda _: None)


if __name__ == "__main__":
    entry_point()
