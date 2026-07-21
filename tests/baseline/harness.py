"""Declarative driver for a real adapter with deterministic model output."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from band.adapters.anthropic import AnthropicAdapter
from band.core.types import AdapterFeatures, PlatformMessage

from tests.baseline.decisions import ModelDecision
from tests.baseline.tools import BaselineTools


class DecisionScript:
    """Finite sequence of injected decisions consumed by an adapter turn."""

    def __init__(self, decisions: Sequence[ModelDecision | Exception]) -> None:
        if not decisions:
            raise ValueError("A baseline scenario requires at least one model decision")
        self._decisions = list(decisions)
        self.calls: list[dict[str, Any]] = []

    def next(self, **request: Any) -> ModelDecision | Exception:
        self.calls.append(request)
        if not self._decisions:
            raise AssertionError(
                "Adapter requested more model responses than the scenario supplied"
            )
        return self._decisions.pop(0)

    def assert_consumed(self) -> None:
        assert not self._decisions, "Scenario supplied unused model decisions"


@dataclass(frozen=True)
class Observation:
    """Read-only result of one local adapter turn."""

    tools: BaselineTools
    script: DecisionScript

    def assert_tool_called(self, name: str, **arguments: Any) -> None:
        matches = [call for call in self.tools.tool_calls if call["tool_name"] == name]
        assert matches, f"Expected {name} to be called; got {self.tools.tool_calls}"
        assert any(
            all(call["arguments"].get(key) == value for key, value in arguments.items())
            for call in matches
        ), f"Expected {name}{arguments}; got {matches}"

    def assert_event(self, message_type: str, content: str | None = None) -> None:
        matches = [
            event
            for event in self.tools.events_sent
            if event["message_type"] == message_type
        ]
        assert matches, f"Expected a {message_type} event; got {self.tools.events_sent}"
        if content is not None:
            assert any(content in event["content"] for event in matches), (
                f"Expected {message_type} event containing {content!r}; got {matches}"
            )

    def assert_tool_exposure(
        self, *, memory: bool | None = None, contacts: bool | None = None
    ) -> None:
        """Assert which optional tool groups the adapter exposed to the model."""
        requests = self.tools.schema_requests
        assert requests, "Adapter never requested tool schemas, so nothing was exposed"
        checked = {
            flag: expected
            for flag, expected in {
                "include_memory": memory,
                "include_contacts": contacts,
            }.items()
            if expected is not None
        }
        for flag, expected in checked.items():
            observed = [request[flag] for request in requests]
            assert all(value is expected for value in observed), (
                f"Adapter should request schemas with {flag}={expected} on every "
                f"turn, got {observed} — the scenario's declared capabilities "
                "were not honored"
            )


class BaselineScenario:
    """Runs real Anthropic adapter code with an injected decision script.

    This deliberately mirrors an E2E test's shape: the scenario supplies the
    message and expected observations; construction, model injection, platform
    state, and call recording remain shared plumbing.
    """

    def __init__(
        self,
        decisions: Sequence[ModelDecision | Exception],
        *,
        features: AdapterFeatures | None = None,
        tools: BaselineTools | None = None,
    ) -> None:
        self.script = DecisionScript(decisions)
        self.tools = tools or BaselineTools()
        self.adapter = AnthropicAdapter(features=features)
        self._rooms_started: set[str] = set()

    async def run(
        self,
        content: str,
        *,
        room_id: str = "room-baseline",
        message_id: str = "message-baseline",
        history: list[dict[str, Any]] | None = None,
        participants_msg: str | None = None,
        contacts_msg: str | None = None,
    ) -> Observation:
        """Deliver one platform message and return its observable outcome."""
        await self.adapter.on_started("Baseline Agent", "Offline conformance agent")
        self.adapter._call_anthropic = self._call_anthropic  # type: ignore[method-assign]
        message = PlatformMessage(
            id=message_id,
            room_id=room_id,
            content=content,
            sender_id="user-baseline",
            sender_type="User",
            sender_name="Baseline User",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )
        await self.adapter.on_message(
            message,
            self.tools,
            history or [],
            participants_msg,
            contacts_msg,
            is_session_bootstrap=room_id not in self._rooms_started,
            room_id=room_id,
        )
        # A failed turn is deliberately not committed as started: retry delivery
        # must re-bootstrap from durable history rather than retain partial state.
        self._rooms_started.add(room_id)
        return Observation(tools=self.tools, script=self.script)

    async def run_expecting_failure(
        self,
        content: str,
        *,
        error: type[BaseException] = Exception,
        match: str,
        **run_kwargs: Any,
    ) -> Observation:
        """Deliver a message whose turn must raise, still returning the
        observable outcome so failure paths share the success vocabulary."""
        with pytest.raises(error, match=match):
            await self.run(content, **run_kwargs)
        return Observation(tools=self.tools, script=self.script)

    def history_contents(self, room_id: str = "room-baseline") -> list[str]:
        """The adapter's in-memory history for a room, as plain content strings."""
        return [entry["content"] for entry in self.adapter._message_history[room_id]]

    def assert_complete(self) -> None:
        """Assert that every decision supplied for a multi-turn scenario ran."""
        self.script.assert_consumed()

    async def _call_anthropic(self, **request: Any) -> Any:
        """Translate a neutral decision into Anthropic's native response shape."""
        decision = self.script.next(**request)
        if isinstance(decision, Exception):
            raise decision

        from anthropic.types import TextBlock, ToolUseBlock

        content: list[Any] = []
        for index, call in enumerate(decision.tool_calls, start=1):
            content.append(
                ToolUseBlock(
                    type="tool_use",
                    id=f"baseline-call-{index}",
                    name=call.name,
                    input=call.arguments,
                )
            )
        if decision.text:
            content.append(TextBlock(type="text", text=decision.text))
        return SimpleNamespace(
            stop_reason="tool_use" if decision.tool_calls else "end_turn",
            content=content,
            usage=None,
        )
