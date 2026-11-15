"""Exact engine origins and real plugin bodies survive client disconnection safely."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("parlant.sdk")

from band.integrations.parlant.sessiontools import (
    NO_SESSION_TOOLS_ERROR,
    require_session_tools,
    set_session_tools,
)
from band.integrations.parlant.tools import create_parlant_tools
from band.runtime.cycle import TurnScope
from band.runtime.tools.agent import AgentTools
from band.testing import FakeAgentTools
from tests.integrations.activitykit import LookupInput
from tests.integrations.parlant.enginecontext import activate_engine, bind_engine_tools
from tests.integrations.parlant.helpers import ToolServer

SESSION = "owned-plugin"


class TestPluginBodyOwnership:
    async def test_old_engine_context_uses_original_tools_after_session_rebinding(
        self, plugin_server: ToolServer
    ) -> None:
        original, current = FakeAgentTools(), FakeAgentTools()
        engine = await bind_engine_tools(SESSION, original)
        await bind_engine_tools(SESSION, current)
        activate_engine(engine)

        async def lookup(args: LookupInput) -> str:
            tools = require_session_tools(SimpleNamespace(session_id=SESSION))
            await tools.send_notice(args.note, mentions=["@Alice"])
            return args.note

        await plugin_server.enable(
            create_parlant_tools(custom_tools=[(LookupInput, lookup)])
        )
        result = await plugin_server.call(
            "lookup", session_id=SESSION, arguments={"note": "original"}
        )
        assert result == "original"
        assert [message["content"] for message in original.messages_sent] == [
            "original"
        ]
        assert current.messages_sent == []

    async def test_session_lookup_alone_cannot_authorize_a_plugin_call(
        self, plugin_server: ToolServer
    ) -> None:
        set_session_tools(SESSION, FakeAgentTools())

        async def lookup(args: LookupInput) -> str:
            raise AssertionError("Unattributed plugin body must not run")

        await plugin_server.enable(
            create_parlant_tools(custom_tools=[(LookupInput, lookup)])
        )
        result = await plugin_server.call(
            "lookup", session_id=SESSION, arguments={"note": "unknown"}
        )
        assert result == NO_SESSION_TOOLS_ERROR

    async def test_disconnected_client_leaves_the_actual_body_owned_until_cleanup(
        self, plugin_server: ToolServer
    ) -> None:
        tools = AgentTools("room", MagicMock())
        tools._scope = scope = TurnScope(control_revision=0)
        scope.admit()
        await bind_engine_tools(SESSION, tools)
        entered, cleaning, release, blocked = (asyncio.Event() for _ in range(4))

        async def lookup(args: LookupInput) -> str:
            captured = require_session_tools(SimpleNamespace(session_id=SESSION))
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                try:
                    await captured.send_notice(args.note, mentions=["@Alice"])
                except asyncio.CancelledError:
                    blocked.set()
            return "unreachable"

        await plugin_server.enable(
            create_parlant_tools(custom_tools=[(LookupInput, lookup)])
        )
        call = asyncio.create_task(
            plugin_server.call(
                "lookup", session_id=SESSION, arguments={"note": "stale"}
            )
        )
        try:
            await entered.wait()
            (owner,) = scope.workers
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
            assert not owner.task.done()
            owner.terminate()
            await cleaning.wait()
            assert not owner.task.done()
            await bind_engine_tools(SESSION, FakeAgentTools())
            release.set()
            await blocked.wait()
            assert await owner.drain()
            assert owner.task.cancelled()
        finally:
            release.set()
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
            for owner in scope.workers:
                owner.terminate()
            await asyncio.gather(
                *(owner.task for owner in scope.workers), return_exceptions=True
            )
