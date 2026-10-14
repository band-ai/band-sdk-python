"""Batch-input probe for the Google ADK adapter; see ``batchprobes``.

The adapter runs ADK's real runner; only the model is local, registered under
a fresh name so the request it receives is exactly what ADK would send.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

import pytest

from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput
from tests.framework_conformance.batchprobes import BatchProbe, deliver
from tests.framework_conformance.batchprobes.genaitext import conversation_text


async def recording_adapter(final_text: str) -> tuple[SimpleAdapter[Any], list[str]]:
    """A started adapter whose model ends every call with ``final_text``, and
    the text of each request's conversation, one entry per model call."""
    pytest.importorskip("google.adk")
    from google.adk.models import (  # noqa: PLC0415 -- google_adk extra, absent from some lane venvs
        BaseLlm,
        LLMRegistry,
        LlmResponse,
    )
    from google.genai import (  # noqa: PLC0415 -- google_adk extra, absent from some lane venvs
        types,
    )

    from band.adapters.google_adk import (  # noqa: PLC0415 -- google_adk extra, absent from some lane venvs
        GoogleADKAdapter,
        GoogleADKAdapterConfig,
    )

    model = f"fake-batchprobe-{uuid4().hex}"
    requests: list[str] = []

    class ScriptedModel(BaseLlm):
        @classmethod
        def supported_models(cls) -> list[str]:
            return [re.escape(model)]

        async def generate_content_async(self, llm_request, stream=False):
            requests.append(conversation_text(llm_request.contents))
            yield LlmResponse(
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text=final_text)]
                )
            )

    LLMRegistry.register(ScriptedModel)
    adapter = GoogleADKAdapter(GoogleADKAdapterConfig(model=model))
    await adapter.on_started("Agent", "A test agent")
    return adapter, requests


async def run_google_adk(inp: AgentInput, final_text: str) -> str:
    adapter, requests = await recording_adapter(final_text)
    await deliver(adapter, inp)
    return requests[-1]


PROBES: dict[str, BatchProbe] = {"google_adk": BatchProbe(run=run_google_adk)}
