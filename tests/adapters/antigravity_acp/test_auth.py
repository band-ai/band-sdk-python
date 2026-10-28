"""Deliver only configured provider credentials to the native process."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.antigravity_acp import GEMINI_API_KEY_ENV
from tests.adapters.antigravity_acp.kit import AntigravityRoom
from tests.adapters.antigravity_acp.peerconfig import PeerEvent


class TestAntigravityAuth:
    @pytest.mark.parametrize(
        ("configured", "explicit", "expected"),
        [
            ("configured-key", None, "configured-key"),
            ("configured-key", "env-key", "env-key"),
            (None, None, None),
        ],
    )
    async def test_explicit_env_wins_and_ambient_provider_keys_are_not_inherited(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        monkeypatch: pytest.MonkeyPatch,
        configured: str | None,
        explicit: str | None,
        expected: str | None,
    ) -> None:
        monkeypatch.setenv(GEMINI_API_KEY_ENV, "ambient-key")
        env = {GEMINI_API_KEY_ENV: explicit} if explicit is not None else {}
        peer = await open_peer(config={"gemini_api_key": configured, "env": env})

        await peer.send()

        assert [record["key"] for record in peer.records(PeerEvent.STARTED)] == [
            expected
        ]

    async def test_configured_auth_method_is_used(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        peer = await open_peer(config={"auth_method": "agent-platform"})

        await peer.send()

        assert peer.records(PeerEvent.AUTHENTICATED) == [
            {"event": PeerEvent.AUTHENTICATED, "method": "agent-platform"}
        ]
