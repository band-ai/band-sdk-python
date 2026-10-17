"""Listener failures retain ownership without replaying successful teardown."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.integrations.slack.socket import SlackSocketListener
from band.runtime.startup import arm_adapter
from tests.integrations.slack.shutdown.kit import GatedBrain
from tests.integrations.slack.socketkit import _build_adapter_with_socket, _socket_app


class TestListenerOwnership:
    async def test_global_cleanup_reports_failure_then_retries_only_pending_work(
        self,
    ) -> None:
        brain = GatedBrain()
        adapter, _, clients, _, _ = _build_adapter_with_socket(
            inner=brain, apps=[_socket_app("first"), _socket_app("second")]
        )
        arm_adapter(brain)
        clients["second"].close = AsyncMock(side_effect=[OSError("Try again"), None])
        adapter._socket_listeners = [
            SlackSocketListener(app=app, client=clients[app.slug])
            for app in adapter.config.apps
        ]
        with pytest.raises(ExceptionGroup, match="retry host shutdown"):
            await adapter.cleanup_all()
        assert brain.cleanup_calls == 1
        await adapter.cleanup_all()
        assert brain.cleanup_calls == 1
        assert clients["first"].close.await_count == 1
        assert clients["second"].close.await_count == 2
