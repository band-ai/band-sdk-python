"""Batch-input probe for the Google ADK adapter; see ``batchprobes``.

The adapter runs ADK's real runner and its real model class; only the model's
network call is replaced, so the request it receives is exactly what ADK would
send.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput
from tests.framework_conformance.batchprobes import BatchProbe, deliver
from tests.framework_conformance.batchprobes.genaitext import conversation_text


@asynccontextmanager
async def recording_adapter(
    final_text: str,
) -> AsyncIterator[tuple[SimpleAdapter[Any], list[str]]]:
    """A started adapter whose model ends every call with ``final_text``, and
    the text of each request's conversation, one entry per model call."""
    pytest.importorskip("google.adk")
    from google.adk.models import (  # noqa: PLC0415 -- google_adk extra, absent from some lane venvs
        LlmResponse,
    )
    from google.genai import (  # noqa: PLC0415 -- google_adk extra, absent from some lane venvs
        types,
    )

    from band.adapters.google_adk import (  # noqa: PLC0415 -- google_adk extra, absent from some lane venvs
        GoogleADKAdapter,
        GoogleADKAdapterConfig,
        _require_adk,
    )

    _, _, _, model_class, _ = _require_adk()
    requests: list[str] = []

    async def scripted_generate(self, llm_request, stream=False):
        requests.append(conversation_text(llm_request.contents))
        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=final_text)]
            )
        )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(model_class, "generate_content_async", scripted_generate)
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig())
        await adapter.on_started("Agent", "A test agent")
        yield adapter, requests


async def run_google_adk(inp: AgentInput, final_text: str) -> str:
    async with recording_adapter(final_text) as (adapter, requests):
        await deliver(adapter, inp)
    return requests[-1]


PROBES: dict[str, BatchProbe] = {"google_adk": BatchProbe(run=run_google_adk)}
