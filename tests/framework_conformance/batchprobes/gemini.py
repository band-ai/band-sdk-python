"""Batch-input probe for the Gemini adapter; see ``batchprobes``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput
from tests.framework_conformance.batchprobes import BatchProbe, deliver
from tests.framework_conformance.batchprobes.genaitext import conversation_text


async def recording_adapter(final_text: str) -> tuple[SimpleAdapter[Any], list[str]]:
    """A started adapter whose model ends every call with ``final_text``, and
    the text of each request's conversation, one entry per model call."""
    pytest.importorskip("google.genai")
    from google.genai import (  # noqa: PLC0415 -- gemini extra, absent from some lane venvs
        types,
    )

    from band.adapters.gemini import (  # noqa: PLC0415 -- gemini extra, absent from some lane venvs
        GeminiAdapter,
    )

    reply = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text=final_text)]
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )
    requests: list[str] = []

    async def generate_content(**request: Any) -> types.GenerateContentResponse:
        # Read at call time: the adapter appends this reply to the same list.
        requests.append(conversation_text(request["contents"]))
        return reply

    adapter = GeminiAdapter()
    adapter.client = cast(
        "Any",
        SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(
                    generate_content=AsyncMock(side_effect=generate_content)
                )
            )
        ),
    )
    await adapter.on_started("Agent", "A test agent")
    return adapter, requests


async def run_gemini(inp: AgentInput, final_text: str) -> str:
    adapter, requests = await recording_adapter(final_text)
    await deliver(adapter, inp)
    return requests[-1]


PROBES: dict[str, BatchProbe] = {"gemini": BatchProbe(run=run_gemini)}
