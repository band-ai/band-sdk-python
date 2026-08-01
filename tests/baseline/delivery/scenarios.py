"""Shared delivery / text-fallback scenario rows for the adapter matrix.

One table drives Copilot, Codex, OpenCode, and the ACP client. Runners interpret
``InProcessAction``; ``TurnOutcome.assert_matches`` checks ``expected_shape``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tests.baseline.adapter import NON_AGENT_ADAPTERS, Adapter

# ACP client toolkit path — excluded from ``Adapter`` (see NON_AGENT_ADAPTERS).
ACP_CLIENT = "acp"
assert ACP_CLIENT in NON_AGENT_ADAPTERS

DELIVERY_ADAPTERS: tuple[str, ...] = (
    Adapter.CODEX,
    Adapter.COPILOT_SDK,
    Adapter.OPENCODE,
    ACP_CLIENT,
)

# Default coverage for a scenario — every delivery adapter.
ALL_DELIVERY = frozenset(DELIVERY_ADAPTERS)


class InProcessAction(StrEnum):
    POST_OK = "post_ok"
    POST_FAIL = "post_fail"
    NO_POST = "no_post"
    NON_POSTING_TOOL = "non_posting_tool"
    SEND_MESSAGE_OK = "send_message_ok"


AGENT_TEXT = "agent wrap-up text"
TOOL_POST_TEXT = "via-tool"
DIRECT_POST_TEXT = "via-send"
LEGACY_POST_TEXT = "via-legacy"


@dataclass(frozen=True)
class DeliveryScenario:
    """One tool-first reply contract cell.

    ``adapters`` is the positive set that runs this row — never filtered out later.
    """

    id: str
    action: InProcessAction
    agent_text: str
    expect_receipt: bool
    expect_text_fallback: bool
    adapters: frozenset[str] = ALL_DELIVERY

    def covers(self, pool: frozenset[str] = ALL_DELIVERY) -> tuple[str, ...]:
        """Adapters from this row that sit in ``pool``, in matrix order."""
        wanted = self.adapters & pool
        return tuple(adapter for adapter in DELIVERY_ADAPTERS if adapter in wanted)

    @property
    def fails_room_post(self) -> bool:
        return self.action is InProcessAction.POST_FAIL

    @property
    def expected_shape(self) -> tuple[str, ...]:
        """What ``TurnOutcome.shape`` must equal for this row."""
        bits: list[str] = []
        if self.expect_receipt:
            bits.append("delivered")
        if self.expect_text_fallback:
            bits.append(f"fallback:{self.agent_text}")
        return tuple(bits)

    @property
    def expected_texts(self) -> tuple[str, ...]:
        """Every user-visible text message expected for this row, in order."""
        if self.expect_text_fallback:
            return (self.agent_text,)
        match self.action:
            case InProcessAction.POST_OK:
                return (TOOL_POST_TEXT,)
            case InProcessAction.SEND_MESSAGE_OK:
                return (DIRECT_POST_TEXT,)
            case _:
                return ()


SCENARIOS: tuple[DeliveryScenario, ...] = (
    DeliveryScenario(
        "posted",
        InProcessAction.POST_OK,
        AGENT_TEXT,
        expect_receipt=True,
        expect_text_fallback=False,
    ),
    DeliveryScenario(
        "failed_post",
        InProcessAction.POST_FAIL,
        AGENT_TEXT,
        expect_receipt=True,
        expect_text_fallback=True,
    ),
    DeliveryScenario(
        "no_post",
        InProcessAction.NO_POST,
        AGENT_TEXT,
        expect_receipt=True,
        expect_text_fallback=True,
    ),
    DeliveryScenario(
        "non_posting",
        InProcessAction.NON_POSTING_TOOL,
        AGENT_TEXT,
        expect_receipt=True,
        expect_text_fallback=True,
    ),
    # Direct room post (Copilot ask_user) — other adapters have no ask_user seam.
    DeliveryScenario(
        "send_message_ok",
        InProcessAction.SEND_MESSAGE_OK,
        AGENT_TEXT,
        expect_receipt=True,
        expect_text_fallback=False,
        adapters=frozenset({Adapter.COPILOT_SDK}),
    ),
)
