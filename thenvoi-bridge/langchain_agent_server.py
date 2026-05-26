"""Real LangChain agent server using gpt-5.4-mini.

Serves a ``/invoke`` endpoint matching the LangServe format
and a ``/health`` check for readiness probes.

Requires:
    pip install fastapi uvicorn langchain-openai

Run with:
    OPENAI_API_KEY=sk-... python langchain_agent_server.py

The server listens on port 8001 by default. Override with ``--port``.
"""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn
from fastapi import FastAPI, HTTPException
from langchain_openai import ChatOpenAI

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="LangChain Agent Server")

_llm: ChatOpenAI | None = None


def _get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        _llm = ChatOpenAI(model="gpt-5.4-mini", temperature=0)
    return _llm


@app.get("/health")
async def health() -> dict[str, str]:
    """Readiness probe."""
    return {"status": "ok"}


@app.post("/invoke")
async def invoke(body: dict) -> dict:
    """LangServe-compatible /invoke endpoint.

    Accepts:
        {
            "input": "user message",
            "config": {"configurable": {"thread_id": "..."}},
            "metadata": {...}
        }

    Returns:
        {"output": "agent response"}
    """
    prompt = body.get("input", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="'input' field is required")

    metadata = body.get("metadata", {})
    config = body.get("config", {})

    logger.info("Received prompt: %s", prompt[:200])
    logger.info("Metadata: %s", metadata)
    logger.info("Config: %s", config)

    try:
        llm = _get_llm()
        response = await llm.ainvoke(prompt)
        output = response.content if hasattr(response, "content") else str(response)
    except Exception:
        logger.exception("LLM invocation failed")
        raise HTTPException(status_code=500, detail="LLM invocation failed")

    logger.info("Response: %s", output[:200])
    return {"output": output}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LangChain Agent Server")
    parser.add_argument("--port", type=int, default=8001, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)
