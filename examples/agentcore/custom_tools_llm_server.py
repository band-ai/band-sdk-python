# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "band-sdk[anthropic]",
#   "fastapi>=0.110",
#   "uvicorn>=0.29",
#   "pydantic>=2",
# ]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""AgentCore container variant with a custom tool wired in.

Same shape as ``agentcore_llm_server.py`` (see that file for the full
lifecycle/transport walkthrough). The only difference is ``_build_adapter``
passing ``additional_tools`` — see BUILDING.md's "Adding custom tools"
section.

Environment variables: same as ``agentcore_llm_server.py``.

Run locally::

    BAND_AGENT_ID=... BAND_API_KEY=... ANTHROPIC_API_KEY=... \\
        uv run python examples/agentcore/custom_tools_llm_server.py
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
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


class WeatherInput(BaseModel):
    """Get the weather for a city."""

    city: str


async def get_weather(args: WeatherInput) -> str:
    """Deterministic stand-in for a real weather API."""
    return f"{args.city}: sunny, 22°C"


def _build_adapter(settings: Settings) -> AnthropicAdapter:
    """Like the sibling's, plus ``additional_tools`` wiring the custom tool."""
    emit = Emit.TOOL_CALLS if settings.emit_execution else ()
    return AnthropicAdapter(
        model=settings.anthropic_model,
        provider_key=settings.anthropic_api_key,
        prompt=settings.system_prompt or None,
        emit=emit,
        additional_tools=[(WeatherInput, get_weather)],
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    return {"status": "Healthy"}


@app.post("/invocations")
async def invocations(request: Request) -> dict[str, Any]:
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
