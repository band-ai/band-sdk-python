"""What the approval machinery borrows from the adapter, per room."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from band.core.protocols import AgentToolsProtocol
from band.integrations.opencode import OpencodeClientProtocol


@dataclass
class ApprovalPorts:
    """What the approval machinery needs from the adapter, per room.

    The two mention sources are explicit: ``turn_mentions`` is the open turn's
    senders, for asks; a reply confirmation mentions the control message's own
    sender instead.
    """

    room_id: str
    client: Callable[[], OpencodeClientProtocol | None]
    tools: Callable[[], AgentToolsProtocol | None]
    turn_mentions: Callable[[], list[dict[str, str]]]
    release_turn_wait: Callable[[], None]
    fail_turn: Callable[[str], None]
    abort_session: Callable[[], Awaitable[None]]
    is_own_band_tool: Callable[[str], bool]
