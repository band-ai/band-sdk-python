"""Keep native provider state scoped to one Band agent and room."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from tests.adapters.antigravity_acp.kit import AntigravityRoom
from tests.adapters.antigravity_acp.peerconfig import PeerEvent
from tests.framework_conformance.turnprobes import ROOM_ID, turn_tools


class TestAntigravityHome:
    async def test_unbound_platform_identity_cannot_start_a_shared_home(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        peer = await open_peer()
        peer.adapter.platform = None

        with pytest.raises(RuntimeError, match="platform connection not available"):
            await peer.send(tools=turn_tools())

        assert not Path(peer.adapter.config.gemini_home).exists()

    @pytest.mark.parametrize("room_id", ["a/b", r"a\b"])
    async def test_room_identity_cannot_alias_a_nested_home(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        room_id: str,
    ) -> None:
        peer = await open_peer()

        with pytest.raises(ValueError, match="one path component"):
            await peer.send(room_id=room_id)

        assert peer.records(PeerEvent.STARTED, room_id) == []

    async def test_two_rooms_receive_distinct_native_homes(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        peer = await open_peer(agent_id="agent-one")

        await asyncio.gather(peer.send(room_id="room-a"), peer.send(room_id="room-b"))

        root = Path(peer.adapter.config.gemini_home)
        assert peer.records(PeerEvent.STARTED, "room-a") == [
            {
                "event": PeerEvent.STARTED,
                "home": str(root / "agent-one" / "room-a"),
                "key": None,
            }
        ]
        assert peer.records(PeerEvent.STARTED, "room-b") == [
            {
                "event": PeerEvent.STARTED,
                "home": str(root / "agent-one" / "room-b"),
                "key": None,
            }
        ]

    async def test_two_agents_in_one_room_receive_distinct_homes(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        tmp_path: Path,
    ) -> None:
        home = tmp_path / "native"
        first = await open_peer(agent_id="agent-one", config={"gemini_home": str(home)})
        second = await open_peer(
            agent_id="agent-two", config={"gemini_home": str(home)}
        )

        await asyncio.gather(first.send(), second.send())

        assert first.records(PeerEvent.STARTED) == [
            {
                "event": PeerEvent.STARTED,
                "home": str(home / "agent-one" / ROOM_ID),
                "key": None,
            }
        ]
        assert second.records(PeerEvent.STARTED) == [
            {
                "event": PeerEvent.STARTED,
                "home": str(home / "agent-two" / ROOM_ID),
                "key": None,
            }
        ]

    @pytest.mark.skipif(os.name == "nt", reason="POSIX directory permission bits")
    async def test_existing_provider_directories_become_private(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        tmp_path: Path,
    ) -> None:
        home = tmp_path / "native"
        agent_home = home / "agent-one"
        room_home = agent_home / ROOM_ID
        room_home.mkdir(parents=True)
        for path in (home, agent_home, room_home):
            path.chmod(0o777)
        peer = await open_peer(agent_id="agent-one", config={"gemini_home": str(home)})

        await peer.send()

        assert [
            path.stat().st_mode & 0o777 for path in (home, agent_home, room_home)
        ] == [0o700, 0o700, 0o700]

    @pytest.mark.parametrize(
        "agent_id", ["", ".", "../other", "a/b", r"a\b", "C:other"]
    )
    async def test_agent_identity_cannot_alias_a_path(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        agent_id: str,
    ) -> None:
        peer = await open_peer(agent_id=agent_id)

        with pytest.raises(ValueError, match="one path component"):
            await peer.send()

        assert peer.records(PeerEvent.STARTED) == []

    async def test_cleanup_preserves_the_native_home(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        peer = await open_peer()
        await peer.send()
        [started] = peer.records(PeerEvent.STARTED)
        home = Path(started["home"])

        await peer.adapter.cleanup_all()

        assert home.is_dir()
        assert (home / "peer-sessions.json").read_text() == '["fake-session-1"]'
