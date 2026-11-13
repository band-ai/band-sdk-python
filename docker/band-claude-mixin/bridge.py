"""Connect SDK-managed Claude Code sessions to a Band identity."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
from pathlib import Path

import httpx
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent
from band.adapters.claude_sdk import (
    ClaudeCLIOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)
from band.core.types import Emit
from band.runtime.shutdown import run_with_graceful_shutdown

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    BAND_API_KEY: SecretStr
    BAND_REST_URL: str = "https://app.band.ai"
    BAND_WS_URL: str = "wss://app.band.ai/api/v1/socket/websocket"


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", help="Existing Band agent UUID")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--model", default="sonnet")
    parser.add_argument(
        "--check-claude",
        action="store_true",
        help="Check Claude SDK authentication without connecting to Band",
    )
    parser.add_argument(
        "--check-band",
        action="store_true",
        help="Check Band identity without listening or sending messages",
    )
    args = parser.parse_args(argv)
    if not args.check_claude and not args.agent_id:
        parser.error("--agent-id is required unless --check-claude is used")
    args.workspace = args.workspace.resolve(strict=True)
    if not args.workspace.is_dir():
        parser.error("--workspace must be a directory")
    return args


async def main(args: argparse.Namespace) -> None:
    claude = shutil.which("claude")
    if not claude:
        raise ValueError("This mixin needs a workload that supplies Claude Code")
    if args.check_claude:
        result = None
        async for message in query(
            prompt="Reply with exactly OAUTH_OK. Do not use tools.",
            options=ClaudeAgentOptions(
                cli_path=claude,
                cwd=str(args.workspace),
                model=args.model,
                setting_sources=["user"],
            ),
        ):
            if isinstance(message, ResultMessage):
                result = message
        if result is None:
            raise ValueError("Claude Agent SDK returned no result")
        if result.is_error or (result.result or "").strip() != "OAUTH_OK":
            raise ValueError("Claude SDK authentication check failed")
        logger.info("Claude Agent SDK authentication: OAUTH_OK")
        return

    settings = Settings()
    async with httpx.AsyncClient() as client:
        response = await client.get(
            settings.BAND_REST_URL.rstrip("/") + "/api/v1/agent/me",
            headers={"X-API-Key": settings.BAND_API_KEY.get_secret_value()},
            timeout=20,
        )
        response.raise_for_status()
        identity = response.json()["data"]
        if identity["id"] != args.agent_id:
            raise ValueError("Band key belongs to a different agent UUID")
    logger.info("Band authentication OK: @%s", identity["handle"])
    if args.check_band:
        return

    adapter = ClaudeSDKAdapter(
        ClaudeSDKAdapterConfig(
            model=args.model,
            cwd=args.workspace,
            cli=ClaudeCLIOptions(cli_path=claude),
            setting_sources=("user",),
            permission_mode="bypassPermissions",
            custom_section=(
                "Work only in the configured workspace. Never print credentials. "
                "For repair requests, reproduce the failure before changing source, "
                "leave tests unchanged, rerun the full suite, and report the actual result."
            ),
        ),
        emit={Emit.TOOL_CALLS},
    )
    agent = Agent.create(
        adapter=adapter,
        agent_id=args.agent_id,
        api_key=settings.BAND_API_KEY.get_secret_value(),
        rest_url=settings.BAND_REST_URL,
        ws_url=settings.BAND_WS_URL,
    )
    logger.info("Connecting Claude Code to Band; workspace=%s", args.workspace)
    await run_with_graceful_shutdown(agent)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(arguments()))
