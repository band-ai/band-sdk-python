"""Observe model-issued activity requests in the CrewAI OpenAI lane."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic import ValidationError

from tests.e2e.baseline.toolkit.activitywork import ActivityLookupInput, ActivityWork
from tests.e2e.baseline.toolkit.adapters import Adapter
from tests.e2e.baseline.toolkit.observations.tool_calls import ToolCall


@asynccontextmanager
async def activity_model_requests(
    adapter_id: str, work: ActivityWork
) -> AsyncIterator[list[ToolCall] | None]:
    """Capture native IDs before CrewAI strips them from tool executions."""
    if adapter_id != Adapter.CREWAI:
        yield None
        return

    from crewai.events import crewai_event_bus  # noqa: PLC0415 -- CrewAI lane only
    from crewai.events.types.llm_events import (  # noqa: PLC0415
        LLMCallCompletedEvent,
        LLMCallType,
    )
    from openai.types.chat import ChatCompletionMessageFunctionToolCall  # noqa: PLC0415 -- OpenAI-backed CrewAI lane

    calls: list[ToolCall] = []

    def record(_source: object, event: LLMCallCompletedEvent) -> None:
        if event.call_type != LLMCallType.TOOL_CALL or not isinstance(
            event.response, list
        ):
            return
        for raw_call in event.response:
            try:
                call = ChatCompletionMessageFunctionToolCall.model_validate(raw_call)
                if call.function.name != work.tool.name:
                    continue
                args = ActivityLookupInput.model_validate_json(call.function.arguments)
            except ValidationError:
                continue
            if args.private_note == work.note:
                calls.append(ToolCall(work.tool.name, args.model_dump(), call.id))

    crewai_event_bus.on(LLMCallCompletedEvent)(record)
    try:
        yield calls
    finally:
        crewai_event_bus.off(LLMCallCompletedEvent, record)
        assert await asyncio.to_thread(crewai_event_bus.flush, work.deadline_s), (
            "CrewAI model event handlers did not finish"
        )
