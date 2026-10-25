"""The shape every batch-input probe shares.

A probe drives one turn through an adapter's real batch-aware entry points with
a scripted model that ends with the given text, and returns everything the
provider received for that turn as one string. The conformance suites assert
on that string, never on the adapter's internals, so a field the adapter drops
or duplicates on its way to the model is caught.

``delivery_input`` builds the ``AgentInput`` through the real
``DefaultPreprocessor`` over a persisted room, so history and originals take
the production path. ``deliver`` dispatches as the runtime does: one original
through ``on_event``, several through ``on_messages``.

A framework that only some lanes install is imported inside its probe function,
not at module top, so collection works in every lane's venv.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, cast

from band.core.protocols import TurnResultAlreadyReported
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.formatters import InputField
from band.testing.fake_tools import FakeAgentTools
from tests.conftest import make_message_event
from tests.runtime.batching.kit import room_context
from tests.runtime.currentrecords import current_records as current_records
from tests.runtime.helpers import AGENT_ID, ROOM_ID


@dataclass(frozen=True)
class Original:
    """One selected input as a participant sent it."""

    id: str
    sender_id: str
    sender_name: str
    sender_type: str
    content: str
    timestamp: str

    @property
    def record(self) -> dict[str, str | None]:
        """The labeled record the model is expected to be shown for it."""
        return {
            InputField.MESSAGE_ID: self.id,
            InputField.SENDER_ID: self.sender_id,
            InputField.SENDER_NAME: self.sender_name,
            InputField.SENDER_TYPE: self.sender_type,
            InputField.TIMESTAMP: self.timestamp,
            InputField.CONTENT: self.content,
        }


ALICE_FIRST = Original(
    id="m1",
    sender_id="alice",
    sender_name="Alice",
    sender_type="User",
    content="alice-first-ask",
    timestamp="2024-01-01T00:00:01+00:00",
)
HELPER = Original(
    id="m2",
    sender_id="helper",
    sender_name="Helper",
    sender_type="Agent",
    content="helper-fyi-note",
    timestamp="2024-01-01T00:00:02+00:00",
)
ALICE_SECOND = Original(
    id="m3",
    sender_id="alice",
    sender_name="Alice",
    sender_type="User",
    content="alice-second-ask",
    timestamp="2024-01-01T00:00:03+00:00",
)
#: One person sending twice around a different sender type.
BATCH = (ALICE_FIRST, HELPER, ALICE_SECOND)
SINGLETON = (ALICE_FIRST,)

EARLIER_QUESTION = "earlier-question-before-the-turn"
EARLIER_ANSWER = "earlier-answer-before-the-turn"
LATER_PENDING = "later-pending-after-the-turn"

#: The room as persisted: history, then the originals, then a later message.
STORED_ROOM = (
    replace(ALICE_FIRST, id="h1", content=EARLIER_QUESTION),
    replace(HELPER, id="h2", content=EARLIER_ANSWER),
    *BATCH,
    replace(ALICE_FIRST, id="m4", content=LATER_PENDING),
)

FINAL_TEXT = "scripted-final-text"

#: Runs one turn and returns all text the provider received for it.
BatchProbeRun = Callable[[AgentInput, str], Awaitable[str]]


@dataclass(frozen=True)
class BatchProbe:
    """``run`` drives one scripted turn over ``inp`` ending with the given
    final text. ``relays`` marks an adapter that posts that text when no tool
    replied, addressed to the turn's originating senders."""

    run: BatchProbeRun
    relays: bool = False


def stored_message(original: Original) -> dict[str, Any]:
    return {
        "id": original.id,
        "sender_id": original.sender_id,
        "sender_type": original.sender_type,
        "sender_name": original.sender_name,
        "content": original.content,
    }


async def delivery_input(originals: Sequence[Original]) -> AgentInput:
    """The bootstrap input the production preprocessor builds for ``originals``,
    delivering through a fake room's tools."""
    events = [
        make_message_event(
            room_id=ROOM_ID,
            msg_id=original.id,
            sender_id=original.sender_id,
            sender_type=original.sender_type,
            content=original.content,
            inserted_at=original.timestamp,
        )
        for original in originals
    ]
    ctx = room_context(stored=[stored_message(o) for o in STORED_ROOM])
    inp = await DefaultPreprocessor().process_batch(ctx, events, AGENT_ID)
    assert inp is not None
    return replace(
        inp,
        tools=FakeAgentTools(
            room_id=ROOM_ID,
            participants=[
                {
                    "id": ALICE_FIRST.sender_id,
                    "name": ALICE_FIRST.sender_name,
                    "type": ALICE_FIRST.sender_type,
                    "role": "member",
                    "status": "active",
                },
                {
                    "id": HELPER.sender_id,
                    "name": HELPER.sender_name,
                    "type": HELPER.sender_type,
                    "role": "member",
                    "status": "active",
                },
                {
                    "id": AGENT_ID,
                    "name": "SDK Agent",
                    "type": "Agent",
                    "role": "member",
                    "status": "active",
                },
            ],
        ),
    )


async def deliver(adapter: SimpleAdapter[Any], inp: AgentInput) -> None:
    """Dispatch as the runtime does: one original is a singleton turn.

    The scripted model often ends without replying; that verdict is not what a
    probe observes, so it is let go.
    """
    with contextlib.suppress(TurnResultAlreadyReported):
        if len(inp.messages) > 1:
            await adapter.on_messages(inp)
        else:
            await adapter.on_event(inp)


def posted_messages(inp: AgentInput) -> list[dict[str, Any]]:
    """The room posts the turn made through its fake tools."""
    return cast(FakeAgentTools, inp.tools).messages_sent
