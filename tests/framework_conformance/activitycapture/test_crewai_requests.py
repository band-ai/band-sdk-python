"""CrewAI activity repeats can be traced back to real provider requests."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from band.core.defaultmodels import OPENAI_MODEL
from tests.e2e.baseline.toolkit.activitywork import (
    ActivityLookupInput,
    ActivityWork,
    Operation,
)
from tests.e2e.baseline.toolkit.adapters import Adapter
from tests.e2e.baseline.toolkit.crewairequests import activity_model_requests
from tests.e2e.baseline.toolkit.observations.tool_calls import ToolCall
from tests.framework_conformance.activitycapture.lookup import HeldLookupPeer

crewai = pytest.importorskip("crewai")

from tests.crewaiscript import native_tool_call


class TestCrewAIRequests:
    async def test_captures_distinct_provider_requests_only_for_this_work(
        self, held_lookup: tuple[ActivityWork, HeldLookupPeer], httpx_mock: HTTPXMock
    ) -> None:
        work, _ = held_lookup
        request = ActivityLookupInput(key=Operation.A, private_note=work.note)
        args = request.model_dump()
        name = work.tool.name
        model = crewai.LLM(
            model=OPENAI_MODEL, base_url="https://model.invalid/v1", api_key="test"
        )
        httpx_mock.add_response(
            url="https://model.invalid/v1/chat/completions",
            json={
                "id": "response",
                "object": "chat.completion",
                "created": 1,
                "model": OPENAI_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                native_tool_call("first", name, args),
                                native_tool_call(
                                    "other-room-test",
                                    name,
                                    {**args, "private_note": "unrelated"},
                                ),
                                native_tool_call("second", name, args),
                            ],
                        },
                    }
                ],
            },
        )
        async with activity_model_requests(Adapter.CREWAI, work) as model_requests:
            await model.acall(work.instruction(Operation.A))
        assert model_requests == [
            ToolCall(name, args, "first"),
            ToolCall(name, args, "second"),
        ]
