# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "band-sdk[anthropic]",
#   "fastapi>=0.110",
#   "uvicorn>=0.29",
# ]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""AgentCore container that runs the Band SDK per invocation.

The bridge (dumb pipe) forwards raw Band WS events to this container over
HTTP. On each POST /invocations the container hands the forwarded event to the
SDK's :class:`OneShotInvoker`, which reconstructs a typed message, fetches
participants + history via REST, runs the adapter's LLM tool loop, and honors
the platform's lifecycle markers (claim / processed / failed / drain) so
concurrent invocations don't duplicate work.

Each invocation is one-shot — no per-room state is kept across calls. The SDK
owns all the lifecycle logic; this file is just the AgentCore Runtime transport
(``/ping`` + ``/invocations``) and env-driven adapter construction.

Environment variables:
    BAND_AGENT_ID — agent's Band identity (required)
    BAND_API_KEY  — Band REST API key (required)
    ANTHROPIC_API_KEY — Anthropic API key for the LLM loop (required)
    BAND_WS_URL   — defaults to wss://app.band.ai/api/v1/socket/websocket
                       (unused by the container; reserved for SDK consistency)
    BAND_REST_URL — defaults to https://app.band.ai
    ANTHROPIC_MODEL  — defaults to claude-sonnet-4-5-20250929
    SYSTEM_PROMPT    — optional custom system prompt for the adapter
    EMIT_EXECUTION   — "true" (default) emits tool_call/tool_result as platform
                       events; set "false" to silence them
    PORT             — defaults to 8080 (AgentCore Runtime contract)

Run locally::

    BAND_AGENT_ID=... BAND_API_KEY=... ANTHROPIC_API_KEY=... \\
        uv run python examples/agentcore/agentcore_llm_server.py
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import LogSettings
from band.adapters.anthropic import AnthropicAdapter
from band.core.types import Emit
from band.platform.link import BandLink
from band.runtime.oneshot import OneShotEnvelopeError, OneShotInvoker

LogSettings().for_application().configure()
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    band_agent_id: str
    band_api_key: str
    anthropic_api_key: str
    band_ws_url: str = "wss://app.band.ai/api/v1/socket/websocket"
    band_rest_url: str = "https://app.band.ai"
    anthropic_model: str = "claude-sonnet-4-5-20250929"
    system_prompt: str = ""
    emit_execution: bool = True
    port: int = 8080
    host: str = "0.0.0.0"


def _build_adapter(settings: Settings) -> AnthropicAdapter:
    """Build the AnthropicAdapter from env config.

    Enables ``Emit.TOOL_CALLS`` by default so every tool_call and tool_result
    is posted to the room as a platform event (visible in the Band UI).
    Set ``EMIT_EXECUTION=false`` to disable.
    """
    emit = Emit.TOOL_CALLS if settings.emit_execution else ()
    return AnthropicAdapter(
        model=settings.anthropic_model,
        provider_key=settings.anthropic_api_key,
        prompt=settings.system_prompt or None,
        emit=emit,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize link + adapter + invoker; prime the adapter."""
    settings = Settings()

    link = BandLink(
        agent_id=settings.band_agent_id,
        api_key=settings.band_api_key,
        ws_url=settings.band_ws_url,
        rest_url=settings.band_rest_url,
    )
    invoker = OneShotInvoker(
        link=link,
        adapter=_build_adapter(settings),
        agent_id=settings.band_agent_id,
    )
    await invoker.startup()
    logger.info(
        "Container ready: agent_id=%s name=%s",
        settings.band_agent_id,
        invoker.agent_name,
    )

    app.state.invoker = invoker

    try:
        yield
    finally:
        await invoker.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/ping")
async def ping() -> dict[str, str]:
    """AgentCore Runtime health probe."""
    return {"status": "Healthy"}


@app.post("/invocations")
async def invocations(request: Request) -> dict[str, Any]:
    """Process one forwarded event from the bridge.

    Body shape (from bridge_core.bridge.AgentRunner._serialize_event)::

        {
          "event_type": "message_created" | "room_added" | ...,
          "agent_id": "<recipient agent id>",
          "room_id": "<chat room id or null>",
          "payload": {...},   # Pydantic model_dump of the event payload
          "raw": {...},
          "forwarded_at": "ISO-8601"
        }
    """
    invoker: OneShotInvoker = app.state.invoker
    body = await request.json()
    try:
        return await invoker.handle_event(body)
    except OneShotEnvelopeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("invocation failed")
        raise HTTPException(status_code=500, detail="invocation failed")


def main() -> None:
    settings = Settings()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
