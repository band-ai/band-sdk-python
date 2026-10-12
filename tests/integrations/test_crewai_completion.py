"""Empty completion behavior through real CrewAI execution and Band tools."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

pytest.importorskip("crewai", reason="crewai not installed (band-sdk[crewai])")

from crewai import Agent
from crewai.llms.base_llm import BaseLLM

from band.adapters.crewai import CrewAIAdapter
from band.core.protocols import TurnResultAlreadyReported
from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import BandTool, TurnEffect
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input, turn_tools

ANSWER = "The vault code is 4471-ECHO."


class ScriptedLLM(BaseLLM):
    def __init__(self) -> None:
        super().__init__(model="offline-completion-test")
        self.responses: deque[Any] = deque()

    def supports_function_calling(self) -> bool:
        return True

    def call(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    async def acall(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        return self.call(messages, *args, **kwargs)


def tool_call(name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": "call-offline",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }
    ]


@pytest.fixture
def model() -> ScriptedLLM:
    return ScriptedLLM()


class WorkInput(BaseModel):
    """Perform an action."""

    fail: bool = False


@pytest.fixture
def executions() -> list[bool]:
    return []


@pytest.fixture
async def adapter(
    model: ScriptedLLM, executions: list[bool]
) -> AsyncIterator[CrewAIAdapter]:
    @declares_turn_effect(TurnEffect.ACT)
    async def perform_work(args: WorkInput) -> str:
        executions.append(args.fail)
        if args.fail:
            raise RuntimeError("action failed")
        return "done"

    adapter = CrewAIAdapter(additional_tools=[(WorkInput, perform_work)])
    adapter._crewai_agent = Agent(
        role="Tester",
        goal="Validate turns",
        backstory="Use Band tools.",
        llm=model,
        tools=adapter.create_crewai_tools(),
    )
    try:
        yield adapter
    finally:
        await adapter.on_cleanup(ROOM_ID)


@pytest.mark.parametrize("blank", ["", None, "\n", " \t\r\n"])
async def test_blank_completion_recovers_with_one_reply(
    adapter: CrewAIAdapter, model: ScriptedLLM, blank: str | None
) -> None:
    model.responses.extend(
        [
            blank,
            tool_call(
                BandTool.SEND_MESSAGE, {"content": ANSWER, "mentions": ["@alice"]}
            ),
            "\n",
        ]
    )
    tools = turn_tools()

    await adapter.on_event(turn_input(tools))

    assert tools.chat == [ANSWER]
    assert failure_reports(tools) == []
    assert not model.responses


@pytest.mark.parametrize("blank", ["", None, "\n", " \t\r\n"])
async def test_blank_completions_exhaust_with_one_missing_reply(
    adapter: CrewAIAdapter, model: ScriptedLLM, blank: str | None
) -> None:
    model.responses.extend([blank, blank])
    tools = turn_tools()

    with pytest.raises(TurnResultAlreadyReported):
        await adapter.on_event(turn_input(tools))

    assert tools.chat == []
    assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
    assert not model.responses


@pytest.mark.parametrize(
    ("name", "arguments", "completes", "chat"),
    [
        (
            BandTool.SEND_MESSAGE,
            {"content": ANSWER, "mentions": ["@alice"]},
            True,
            [ANSWER],
        ),
        (BandTool.NO_REPLY, {"reason": "FYI"}, True, []),
        (BandTool.CREATE_CHATROOM, {}, True, []),
        (BandTool.GET_PARTICIPANTS, {}, False, []),
        (BandTool.SEND_MESSAGE, {"content": ANSWER, "mentions": []}, False, []),
    ],
    ids=["reply", "decline", "action", "read-only", "failed-send"],
)
async def test_tool_activity_prevents_blank_completion_retry(
    adapter: CrewAIAdapter,
    model: ScriptedLLM,
    name: BandTool,
    arguments: dict[str, Any],
    completes: bool,
    chat: list[str],
) -> None:
    model.responses.extend([tool_call(name, arguments), "\n"])
    tools = turn_tools()

    if completes:
        await adapter.on_event(turn_input(tools))
    else:
        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

    assert tools.chat == chat
    assert failure_reports(tools) == ([] if completes else [MISSING_REPLY_FAILURE])
    assert not model.responses


@pytest.mark.parametrize("fail", [False, True], ids=["action", "failed-action"])
async def test_custom_tool_is_not_repeated_after_blank_output(
    adapter: CrewAIAdapter, model: ScriptedLLM, executions: list[bool], fail: bool
) -> None:
    model.responses.extend(
        [tool_call(get_custom_tool_name(WorkInput), {"fail": fail}), "\n"]
    )
    tools = turn_tools()

    if fail:
        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))
    else:
        await adapter.on_event(turn_input(tools))

    assert executions == [fail]
    assert failure_reports(tools) == ([MISSING_REPLY_FAILURE] if fail else [])
    assert not model.responses
