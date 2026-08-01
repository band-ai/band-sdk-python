"""Intent-oriented delivery observations — drive a turn, assert on the view.

Mirrors the E2E toolkit shape: observation objects own their asserts
(``Delivery.assert_delivered``), and a small turn handle hides ObservingTools
plumbing so tests read as drive → observe → assert.
"""

from __future__ import annotations

from dataclasses import dataclass

from band.core.backends.observing import ObservingTools, delivered
from band.core.contracts.delivery import DeliveryReceipt
from band.runtime.tools import (
    BAND_LIST_CONTACTS,
    BAND_SEND_MESSAGE,
    is_room_posting_tool,
)
from band.testing import FakeAgentTools

from tests.baseline.delivery.outcome import TurnOutcome
from tests.baseline.delivery.scenarios import (
    DIRECT_POST_TEXT,
    DeliveryScenario,
    InProcessAction,
)
from tests.baseline.delivery.tools import (
    BandSendMessageArgs,
    observed_tools,
    VIA_TOOL,
)


@dataclass(frozen=True)
class Delivery:
    """This turn's delivery observation — asserts live with the data."""

    _tools: object

    @property
    def receipt(self) -> DeliveryReceipt | None:
        return delivered(self._tools)  # type: ignore[arg-type]

    def assert_delivered(self, *, as_tool: str = BAND_SEND_MESSAGE) -> DeliveryReceipt:
        """This turn posted to the room successfully."""
        receipt = self.receipt
        assert receipt is not None, "expected a room delivery receipt for this turn"
        assert receipt.tool_name == as_tool, (
            f"expected delivery via {as_tool!r}, got {receipt.tool_name!r}"
        )
        return receipt

    def assert_undelivered(self) -> None:
        """This turn has not successfully posted to the room."""
        receipt = self.receipt
        assert receipt is None, f"expected no delivery receipt, got {receipt!r}"


@dataclass
class Turn:
    """One ObservingTools turn under test — open, drive, observe."""

    observing: ObservingTools
    inner: FakeAgentTools

    @classmethod
    def open(cls, *, fail_room_post: bool = False) -> Turn:
        observing, inner = observed_tools(fail_room_post=fail_room_post)
        return cls(observing, inner)

    @property
    def delivery(self) -> Delivery:
        return Delivery(self.observing)

    async def post(self, content: str = "hi") -> None:
        """Drive one room-posting tool call through the observing proxy."""
        await self.observing.execute_tool_call_structured(
            BAND_SEND_MESSAGE,
            BandSendMessageArgs(content=content).to_arguments(),
        )

    async def run_scenario(self, scenario: DeliveryScenario) -> TurnOutcome:
        """Apply the scenario action and close like a tool-first adapter."""
        await _apply_in_process_action(self.observing, scenario)
        if self.delivery.receipt is None and scenario.agent_text:
            await self.observing.send_message(scenario.agent_text, mentions=["Ada"])
        return outcome_from_observing(self.observing, self.inner)


def delivery_of(tools: object) -> Delivery:
    """Wrap any tools surface (proxy, outer wrapper, bare fake) as a Delivery view."""
    return Delivery(tools)


async def _apply_in_process_action(
    tools: ObservingTools, scenario: DeliveryScenario
) -> None:
    """Drive one scenario action against the in-process observing proxy."""
    match scenario.action:
        case InProcessAction.POST_OK | InProcessAction.POST_FAIL:
            await tools.execute_tool_call_structured(
                BAND_SEND_MESSAGE, VIA_TOOL.to_arguments()
            )
        case InProcessAction.SEND_MESSAGE_OK:
            await tools.send_message(DIRECT_POST_TEXT, mentions=["Ada"])
        case InProcessAction.NON_POSTING_TOOL:
            await tools.execute_tool_call_structured(BAND_LIST_CONTACTS, {})
        case InProcessAction.NO_POST:
            pass


def sent_texts(tools: FakeAgentTools) -> tuple[str, ...]:
    """Text posts made through the in-memory platform boundary."""
    return tuple(message.content for message in tools.messages_sent)


def _room_post_texts(tools: FakeAgentTools) -> tuple[str, ...]:
    """Visible tool-post content, represented by the fake's recorded calls."""
    return tuple(
        str(call.arguments["content"])
        for call in tools.tool_calls
        if is_room_posting_tool(call.tool_name) and "content" in call.arguments
    )


def outcome_from_observing(observing: object, inner: FakeAgentTools) -> TurnOutcome:
    """Project an observed turn from its receipt and visible room writes."""
    receipt = delivered(observing)  # type: ignore[arg-type]
    texts = sent_texts(inner)
    if receipt is not None and not texts:
        texts = _room_post_texts(inner)
    return TurnOutcome(
        texts=texts,
        receipt_tool=receipt.tool_name if receipt else None,
    )


async def run_scenario_on_observing_tools(
    scenario: DeliveryScenario,
) -> TurnOutcome:
    """Plumbing-free driver: scenario → TurnOutcome against ObservingTools alone."""
    return await Turn.open(fail_room_post=scenario.fails_room_post).run_scenario(
        scenario
    )
