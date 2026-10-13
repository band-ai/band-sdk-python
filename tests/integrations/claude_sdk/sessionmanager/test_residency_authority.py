"""Only the current residency of the current room session may act."""

from __future__ import annotations

from tests.adapters.claude_sdk.fakecli import FakeClaude
from tests.adapters.claude_sdk.helpers import exchange
from tests.integrations.claude_sdk.sessionmanager.kit import (
    new_manager,
    thinking,
)


class TestResidencyAuthority:
    async def test_a_retired_or_replaced_residency_loses_authority(
        self,
        claude: FakeClaude,
    ) -> None:
        manager = new_manager()
        claude.script(thinking("one"), thinking("two"), thinking("three"))
        first = manager.room("room-a")
        await exchange(first, "one")
        assert first.speaks_for(1)

        assert first.recover("crashed")
        assert not first.speaks_for(1)
        await exchange(first, "two")
        assert first.speaks_for(2)

        await manager.cleanup_session("room-a")
        rejoined = manager.room("room-a")
        await exchange(rejoined, "three")
        # The rejoined room counts generations from the start again.
        authority = [first.speaks_for(1), rejoined.speaks_for(1)]
        await manager.stop()

        assert rejoined is not first
        assert authority == [False, True]
        assert len(claude.sessions) == 3
