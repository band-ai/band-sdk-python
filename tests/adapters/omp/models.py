"""Record native session and model selection state."""

from __future__ import annotations

import asyncio

from pydantic import JsonValue

from band.adapters.omp import (
    OmpAdapter,
)
from band.core.model_catalog import ModelSelection
from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from band.integrations.ompwire import (
    OmpCommandName,
    OmpUIMethod,
    OmpUIRequest,
)
from tests.adapters.omp.runtime import PendingRuntime


class RecordingRuntime(PendingRuntime):
    """Record native commands, the live model, and the prompt text."""

    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.prompt_text = ""
        self.models_at_prompt: tuple[tuple[str, str] | None, ...] = ()
        self.model: tuple[str, str] | None = None
        self.thinking: str | None = None
        self.levels: tuple[str, ...] = ("off", "low", "high")
        self.session_file: str | None = None

    async def request(self, command, **parameters):
        self.calls.append((command, parameters))
        if command == OmpCommandName.SWITCH_SESSION:
            self.session_file = parameters["sessionPath"]
            return {"cancelled": False}
        if command == OmpCommandName.NEW_SESSION:
            self.session_file = None
            return {"cancelled": False}
        if command == OmpCommandName.SET_MODEL:
            self.model = (parameters["provider"], parameters["modelId"])
            return {}
        if command == OmpCommandName.GET_AVAILABLE_THINKING_LEVELS:
            return {"levels": list(self.levels)}
        if command == OmpCommandName.SET_THINKING_LEVEL:
            self.thinking = parameters["level"]
            return {}
        if command == OmpCommandName.GET_STATE:
            state = {
                "sessionId": "native-session",
                "sessionFile": self.session_file,
                "isSettled": True,
            }
            if self.thinking is not None:
                state["thinkingLevel"] = self.thinking
            if self.model is not None:
                provider, model_id = self.model
                state["model"] = {"id": model_id, "provider": provider}
            return state
        return await super().request(command, **parameters)

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        self.prompt_text = _message
        self.models_at_prompt += (self.model,)
        await super().prompt(_message, on_event=on_event, on_tool=on_tool, on_ui=on_ui)


class SelectionGateRuntime(RecordingRuntime):
    """Hold one native selection command at its request boundary."""

    def __init__(self) -> None:
        super().__init__()
        self.held_command: OmpCommandName | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def request(
        self, command: str, **parameters: JsonValue
    ) -> dict[str, JsonValue]:
        if command == self.held_command:
            self.entered.set()
            await self.release.wait()
        return await super().request(command, **parameters)


class PermissionThenReplyRuntime(RecordingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_ui(
            OmpUIRequest(
                id="ui",
                method=OmpUIMethod.SELECT,
                title="Approve tool?",
                options=[OMP_FORM_APPROVE, OMP_FORM_DENY],
            )
        )
        await super().prompt(_message, on_event=on_event, on_tool=on_tool, on_ui=on_ui)


class SelectingResolver:
    def __init__(self) -> None:
        self.adapter: OmpAdapter | None = None
        self.called = asyncio.Event()
        self.selection = ModelSelection(model="openai/other", reasoning_effort="low")

    async def __call__(self, _request) -> None:
        assert self.adapter is not None
        self.called.set()
        await self.adapter.apply_model_selection(
            self.selection,
            room_id="room-123",
        )


def requests_named(
    runtime: RecordingRuntime, command: str | OmpCommandName
) -> list[dict[str, object]]:
    return [parameters for name, parameters in runtime.calls if name == command]
