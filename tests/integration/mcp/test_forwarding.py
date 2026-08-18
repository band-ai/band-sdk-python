"""Integration tests for the SDK-driven MCP registrar (INT-351, Phase 3).

Covers the acceptance criteria enumerated in INT-351: CLI flag combinations
(``--scope``, ``--tools``, ``--room-id``) produce the expected advertised
tool surface and the expected dispatch behavior when a tool is called.

Drives the FastMCP server in-process via ``mcp._tool_manager.call_tool`` to
exercise the full registration + validation + dispatch path without
requiring an actual stdio subprocess. This is deliberate: today's
integration suite already spawns subprocesses via ``@requires_api``, but
those tests hit a live API. Phase 3 needs to verify the transport wiring
itself, which a live server can't distinguish from legacy handlers. A
lightweight in-process test gives us that signal, and the existing
``@requires_api`` smoke tests catch remaining live-API regressions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

from band_mcp.config import Config
from band_mcp.tools import registrar
from band_mcp.tools.registrar import register_tools


@dataclass
class _FakeAppCtx:
    """Stand-in for ``AppContext`` for in-process dispatch tests."""

    human_tools: Any = None
    agent_tools_by_room: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.agent_tools_by_room is None:
            self.agent_tools_by_room = {}


class _FakeCtx:
    """Stand-in for ``AppContextType`` (the FastMCP Context wrapper)."""

    def __init__(self, app_ctx: _FakeAppCtx) -> None:
        self.request_context = MagicMock()
        self.request_context.lifespan_context = app_ctx


@pytest.fixture(autouse=True)
def _patch_tool_resolvers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect ``get_*_tools`` / cache reset to the ``_FakeAppCtx`` payload.

    We can't use a real ``AppContext`` here because that would require a
    live REST client. The fake app ctx holds pre-built MagicMock instances.
    """

    def fake_get_human_tools(ctx: Any) -> Any:
        app = ctx.request_context.lifespan_context
        return app.human_tools

    def fake_get_agent_tools(
        ctx: Any,
        room_id: str | None,
        *,
        sdk_room_id: str | None = None,  # noqa: ARG001 - mirrors production helper
    ) -> Any:
        app = ctx.request_context.lifespan_context
        return app.agent_tools_by_room.get(room_id) or app.agent_tools_by_room.get("*")

    class NoopAsyncLock:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(registrar, "get_human_tools", fake_get_human_tools)
    monkeypatch.setattr(registrar, "get_agent_tools", fake_get_agent_tools)
    monkeypatch.setattr(
        registrar, "get_agent_tools_lock", MagicMock(return_value=NoopAsyncLock())
    )


# ---------------------------------------------------------------------------
# --scope agent,human (no --tools, no --room-id)
# ---------------------------------------------------------------------------


async def test_scope_agent_human_no_tools_registers_both_surfaces_without_contacts() -> (
    None
):
    mcp = FastMCP(name="t")
    cfg = Config(
        scope=["agent", "human"],
        tools=[],
        agent_key="a",
        user_key="u",
    )
    register_tools(mcp, cfg)

    names = {t.name for t in await mcp.list_tools()}
    # Agent surface present
    assert "band_send_message" in names
    # Human surface present
    assert "band_send_my_chat_message" in names
    # Contacts not present by default
    assert "band_list_my_contacts" not in names
    assert "band_list_contacts" not in names


async def test_scope_agent_human_tools_contacts_exposes_resolve_handle() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(
        scope=["agent", "human"],
        tools=["contacts"],
        agent_key="a",
        user_key="u",
    )
    register_tools(mcp, cfg)
    names = {t.name for t in await mcp.list_tools()}

    assert "band_resolve_handle" in names
    assert "band_list_my_contacts" in names
    assert "band_list_contacts" in names


async def test_scope_agent_human_tools_memory_exposes_memory_tools() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(
        scope=["agent", "human"],
        tools=["memory"],
        agent_key="a",
        user_key="u",
    )
    register_tools(mcp, cfg)
    names = {t.name for t in await mcp.list_tools()}

    assert "band_list_user_memories" in names
    assert "band_store_memory" in names


async def test_scope_agent_human_tools_contacts_memory_exposes_both() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(
        scope=["agent", "human"],
        tools=["contacts", "memory"],
        agent_key="a",
        user_key="u",
    )
    register_tools(mcp, cfg)
    names = {t.name for t in await mcp.list_tools()}

    assert "band_list_my_contacts" in names
    assert "band_list_user_memories" in names


# ---------------------------------------------------------------------------
# --scope human only
# ---------------------------------------------------------------------------


async def test_scope_human_only_does_not_register_agent_tools() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["human"], tools=[], user_key="u")
    register_tools(mcp, cfg)
    names = {t.name for t in await mcp.list_tools()}

    # Human tools present
    assert "band_list_my_chats" in names
    # Agent tools absent
    assert "band_send_message" not in names
    assert "band_get_participants" not in names


async def test_call_agent_tool_in_human_only_scope_is_unknown() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["human"], tools=[], user_key="u")
    register_tools(mcp, cfg)

    # FastMCP surfaces unknown tools as ToolError("Unknown tool: ...").
    with pytest.raises(Exception) as excinfo:
        await mcp._tool_manager.call_tool("band_send_message", {})
    assert "Unknown tool" in str(excinfo.value)


# ---------------------------------------------------------------------------
# --room-id r_pinned: schema strips chat_id/room_id; pin is injected
# ---------------------------------------------------------------------------


async def test_pinned_mode_agent_send_message_dispatches_to_pinned_room() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["agent"], tools=[], agent_key="a", room_id="r_pinned")
    register_tools(mcp, cfg)

    # Schema should NOT advertise chat_id or room_id
    tool = next(t for t in await mcp.list_tools() if t.name == "band_send_message")
    props = tool.inputSchema.get("properties", {})
    assert "chat_id" not in props
    assert "room_id" not in props

    # Dispatch with NO chat_id → pinned room is used.
    agent_tools = MagicMock()
    agent_tools.send_message = AsyncMock(return_value={"ok": True})
    app_ctx = _FakeAppCtx(agent_tools_by_room={"r_pinned": agent_tools})

    result = await mcp._tool_manager.call_tool(
        "band_send_message",
        {"content": "hi", "mentions": ["@bob"]},
        context=_FakeCtx(app_ctx),
    )
    agent_tools.send_message.assert_awaited_once()
    call_kwargs = agent_tools.send_message.await_args.kwargs
    assert "chat_id" not in call_kwargs  # stripped before method call
    # Result serialized to JSON
    assert "ok" in str(result)


async def test_pinned_mode_human_send_message_dispatches_with_pinned_chat_id() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["human"], tools=[], user_key="u", room_id="r_pinned")
    register_tools(mcp, cfg)

    tool = next(
        t for t in await mcp.list_tools() if t.name == "band_send_my_chat_message"
    )
    props = tool.inputSchema.get("properties", {})
    assert "chat_id" not in props

    human_tools = MagicMock()
    human_tools.send_my_chat_message = AsyncMock(return_value={"ok": True})
    app_ctx = _FakeAppCtx(human_tools=human_tools)

    result = await mcp._tool_manager.call_tool(
        "band_send_my_chat_message",
        {"content": "hi", "recipients": "@bob"},
        context=_FakeCtx(app_ctx),
    )
    call_kwargs = human_tools.send_my_chat_message.await_args.kwargs
    assert call_kwargs["chat_id"] == "r_pinned"  # pin injected
    assert "ok" in str(result)


async def test_pinned_mode_room_less_human_tool_unchanged() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["human"], tools=[], user_key="u", room_id="r_pinned")
    register_tools(mcp, cfg)

    tool = next(t for t in await mcp.list_tools() if t.name == "band_list_my_chats")
    props = tool.inputSchema.get("properties", {})
    assert "chat_id" not in props  # it was never there to begin with
    # Tool still listed and callable.
    human_tools = MagicMock()
    human_tools.list_my_chats = AsyncMock(return_value={"data": []})
    app_ctx = _FakeAppCtx(human_tools=human_tools)

    await mcp._tool_manager.call_tool(
        "band_list_my_chats", {}, context=_FakeCtx(app_ctx)
    )
    human_tools.list_my_chats.assert_awaited_once()


# ---------------------------------------------------------------------------
# Unpinned dispatch: chat_id and room_id both route to the same room
# ---------------------------------------------------------------------------


async def test_unpinned_agent_dispatch_via_chat_id() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["agent"], tools=[], agent_key="a")
    register_tools(mcp, cfg)

    agent_tools = MagicMock()
    agent_tools.send_message = AsyncMock(return_value={"id": "msg_1"})
    app_ctx = _FakeAppCtx(agent_tools_by_room={"r_abc": agent_tools})

    await mcp._tool_manager.call_tool(
        "band_send_message",
        {"content": "hi", "mentions": ["@bob"], "chat_id": "r_abc"},
        context=_FakeCtx(app_ctx),
    )
    agent_tools.send_message.assert_awaited_once()


async def test_unpinned_agent_dispatch_via_room_id_alias() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["agent"], tools=[], agent_key="a")
    register_tools(mcp, cfg)

    agent_tools = MagicMock()
    agent_tools.send_message = AsyncMock(return_value={"id": "msg_1"})
    app_ctx = _FakeAppCtx(agent_tools_by_room={"r_xyz": agent_tools})

    # Client sends "room_id" — the alias routes to chat_id internally.
    await mcp._tool_manager.call_tool(
        "band_send_message",
        {"content": "hi", "mentions": ["@bob"], "room_id": "r_xyz"},
        context=_FakeCtx(app_ctx),
    )
    agent_tools.send_message.assert_awaited_once()
