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
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from band.adapters.anthropic import AnthropicAdapter
from band.core.types import AdapterFeatures, Emit
from band.platform.link import BandLink
from band.runtime.oneshot import OneShotEnvelopeError, OneShotInvoker

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise ValueError(f"{name} environment variable is required")
    return value


class WeatherInput(BaseModel):
    """Get the weather for a city."""

    city: str


async def get_weather(args: WeatherInput) -> str:
    """Deterministic stand-in for a real weather API."""
    return f"{args.city}: sunny, 22°C"


def _build_adapter(anthropic_api_key: str) -> AnthropicAdapter:
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
    system_prompt = os.environ.get("SYSTEM_PROMPT")
    emit_execution = os.environ.get("EMIT_EXECUTION", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    emit: frozenset[Emit] = (
        frozenset({Emit.EXECUTION}) if emit_execution else frozenset()
    )
    return AnthropicAdapter(
        model=model,
        api_key=anthropic_api_key,
        prompt=system_prompt,
        features=AdapterFeatures(emit=emit),
        additional_tools=[(WeatherInput, get_weather)],
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    agent_id = _require_env("BAND_AGENT_ID")
    api_key = _require_env("BAND_API_KEY")
    anthropic_api_key = _require_env("ANTHROPIC_API_KEY")
    ws_url = os.environ.get("BAND_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")
    rest_url = os.environ.get("BAND_REST_URL", "https://app.band.ai")

    link = BandLink(
        agent_id=agent_id, api_key=api_key, ws_url=ws_url, rest_url=rest_url
    )
    invoker = OneShotInvoker(
        link=link,
        adapter=_build_adapter(anthropic_api_key),
        agent_id=agent_id,
    )
    await invoker.startup()
    logger.info("Container ready: agent_id=%s name=%s", agent_id, invoker.agent_name)

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
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
