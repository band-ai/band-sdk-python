"""Codex adapter: network context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from tests.adapters.codex.kit import CodexRoom
from tests.adapters.codexturns import (
    event_request,
    turn_completed,
)


class TestNetworkContext:
    async def test_network_context_included_in_approval_metadata(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        """networkContext from approval params is forwarded in metadata."""
        room = await codex_room(
            event_request(
                10,
                "item/commandExecution/requestApproval",
                {
                    "command": "npm install lodash",
                    "cwd": "/workspace",
                    "networkContext": {"domains": ["registry.npmjs.org"]},
                },
            ),
            turn_completed(),
        )

        await room.send("install lodash")
        await room.send("/approve req-10")
        await room.settled()

        [approval_event] = [
            e
            for e in room.events_sent
            if e["metadata"].get("codex_event_type") == "approval_request"
        ]
        assert approval_event["metadata"]["codex_network_context"] == {
            "domains": ["registry.npmjs.org"]
        }
        assert approval_event["metadata"]["codex_command"] == "npm install lodash"
