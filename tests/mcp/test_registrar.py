"""Unit tests for ``band_mcp.tools.registrar``.

Covers Phase 3 (INT-351) acceptance criteria:
- Scope-filtered registration matches ``iter_tool_definitions(surface=...)``.
- ``--tools contacts`` / ``--tools memory`` flow into ``iter_tool_definitions``.
- Agent room-bound tools get a ``chat_id`` field added to the advertised schema.
- ``AliasChoices("chat_id", "room_id")`` accepts both names inbound.
- Pinned mode hides ``chat_id`` from advertised schema for both surfaces.
- Handler invokes ``get_agent_tools(ctx, chat_id)`` / ``get_human_tools(ctx)``.
- Handler strips ``chat_id`` from kwargs before calling ``AgentTools.<method>``.
- Handler keeps room-scoped ``AgentTools`` instances cached across calls.
- Room-less tools are registered unchanged regardless of pin state.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

from band.runtime import tools as runtime_tools  # type: ignore[import-not-found]
from band.runtime.tools import (  # type: ignore[import-not-found]
    TOOL_DEFINITIONS,
    ToolDefinition,
    iter_tool_definitions,
)
from band_mcp.config import Config, ConfigError
from band_mcp.tools import registrar
from band_mcp.tools.registrar import (
    AGENT_ROOM_BOUND_TOOL_NAMES,
    _classify_tool,
    _extend_with_chat_id,
    _pin_existing_chat_id,
    make_handler,
    register_tools,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NoopAsyncLock:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _patch_agent_tools_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        registrar, "get_agent_tools_lock", MagicMock(return_value=_NoopAsyncLock())
    )


def _registered_names(mcp: FastMCP) -> set[str]:
    tools = asyncio.new_event_loop().run_until_complete(mcp.list_tools())
    return {t.name for t in tools}


async def _list_tool(mcp: FastMCP, name: str) -> Any:
    tools = await mcp.list_tools()
    for t in tools:
        if t.name == name:
            return t
    return None


# ---------------------------------------------------------------------------
# Scope filtering
# ---------------------------------------------------------------------------


def test_scope_agent_only_registers_agent_surface() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["agent"], tools=[], agent_key="k")
    register_tools(mcp, cfg)

    expected = {
        d.name
        for d in iter_tool_definitions(
            surface="agent", include_contacts=False, include_memory=False
        )
    }
    assert _registered_names(mcp) == expected


def test_scope_human_only_registers_human_surface() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["human"], tools=[], user_key="k")
    register_tools(mcp, cfg)

    expected = {
        d.name
        for d in iter_tool_definitions(
            surface="human", include_contacts=False, include_memory=False
        )
    }
    assert _registered_names(mcp) == expected


def test_scope_both_registers_union() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["agent", "human"], tools=[], agent_key="a", user_key="u")
    register_tools(mcp, cfg)

    expected = set()
    for s in ("agent", "human"):
        expected |= {
            d.name
            for d in iter_tool_definitions(
                surface=s, include_contacts=False, include_memory=False
            )
        }
    assert _registered_names(mcp) == expected


def test_scope_both_rejects_duplicate_names_across_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_definition = TOOL_DEFINITIONS["band_create_chatroom"]
    human_definition = ToolDefinition(
        name=agent_definition.name,
        input_model=agent_definition.input_model,
        method_name=agent_definition.method_name,
        surface="human",
    )

    def fake_iter_tool_definitions(
        surface: str,
        include_contacts: bool,
        include_memory: bool,
    ) -> list[ToolDefinition]:
        if surface == "agent":
            return [agent_definition]
        return [human_definition]

    monkeypatch.setattr(
        runtime_tools,
        "iter_tool_definitions",
        fake_iter_tool_definitions,
    )

    mcp = FastMCP(name="t")
    cfg = Config(scope=["agent", "human"], tools=[], agent_key="a", user_key="u")

    with pytest.raises(
        ConfigError,
        match="Duplicate tool name across enabled surfaces: band_create_chatroom",
    ):
        register_tools(mcp, cfg)


# ---------------------------------------------------------------------------
# --tools contacts / --tools memory propagation
# ---------------------------------------------------------------------------


def test_tools_contacts_registers_contact_tools() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(
        scope=["agent", "human"],
        tools=["contacts"],
        agent_key="a",
        user_key="u",
    )
    register_tools(mcp, cfg)
    names = _registered_names(mcp)

    assert "band_list_my_contacts" in names
    assert "band_resolve_handle" in names
    # Memory stays off
    assert "band_list_memories" not in names
    assert "band_list_user_memories" not in names


def test_tools_memory_registers_memory_tools() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(
        scope=["agent", "human"],
        tools=["memory"],
        agent_key="a",
        user_key="u",
    )
    register_tools(mcp, cfg)
    names = _registered_names(mcp)

    assert "band_list_memories" in names
    assert "band_list_user_memories" in names
    # Contacts stay off
    assert "band_list_my_contacts" not in names


def test_tools_both_registers_both_groups() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(
        scope=["agent", "human"],
        tools=["contacts", "memory"],
        agent_key="a",
        user_key="u",
    )
    register_tools(mcp, cfg)
    names = _registered_names(mcp)

    assert "band_list_my_contacts" in names
    assert "band_list_memories" in names


def test_tools_empty_disables_both() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(
        scope=["agent", "human"],
        tools=[],
        agent_key="a",
        user_key="u",
    )
    register_tools(mcp, cfg)
    names = _registered_names(mcp)

    assert "band_list_memories" not in names
    assert "band_list_user_memories" not in names
    assert "band_list_my_contacts" not in names


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_agent_room_bound_constant_matches_classifier() -> None:
    for name in AGENT_ROOM_BOUND_TOOL_NAMES:
        definition = TOOL_DEFINITIONS[name]
        is_agent, is_human = _classify_tool(definition)
        assert is_agent is True
        assert is_human is False


def test_agent_room_less_tool_not_classified_room_bound() -> None:
    # band_create_chatroom does not take a room id on the agent surface.
    definition = TOOL_DEFINITIONS["band_create_chatroom"]
    is_agent, is_human = _classify_tool(definition)
    assert is_agent is False
    assert is_human is False


async def test_room_less_agent_tool_uses_none_cache_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent_tools = MagicMock()
    fake_agent_tools.create_chatroom = AsyncMock(return_value="room_created")
    get_agent_tools_spy = MagicMock(return_value=fake_agent_tools)
    monkeypatch.setattr(registrar, "get_agent_tools", get_agent_tools_spy)

    definition = TOOL_DEFINITIONS["band_create_chatroom"]

    from band_mcp.tools.registrar import _invoke

    out = await _invoke(
        surface="agent",
        tool_name=definition.name,
        method_name=definition.method_name,
        input_model=definition.input_model,
        pinned_room_id="r_pinned",
        is_agent_room_bound=False,
        is_human_room_bound=False,
        ctx=MagicMock(),
        kwargs={},
    )

    get_agent_tools_spy.assert_called_once()
    assert get_agent_tools_spy.call_args.args[1] is None
    assert get_agent_tools_spy.call_args.kwargs == {"sdk_room_id": ""}
    fake_agent_tools.create_chatroom.assert_awaited_once_with()
    assert "room_created" in out


def test_human_chat_id_tool_classified_room_bound() -> None:
    definition = TOOL_DEFINITIONS["band_send_my_chat_message"]
    is_agent, is_human = _classify_tool(definition)
    assert is_agent is False
    assert is_human is True


def test_human_room_less_tool_not_classified_room_bound() -> None:
    definition = TOOL_DEFINITIONS["band_list_my_chats"]
    is_agent, is_human = _classify_tool(definition)
    assert is_agent is False
    assert is_human is False


# ---------------------------------------------------------------------------
# Unpinned agent handler: schema + dispatch
# ---------------------------------------------------------------------------


async def test_unpinned_agent_schema_includes_chat_id() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["agent"], tools=[], agent_key="k")
    register_tools(mcp, cfg)

    t = await _list_tool(mcp, "band_send_message")
    assert t is not None
    props = t.inputSchema.get("properties", {})
    required = t.inputSchema.get("required", [])
    assert "chat_id" in props
    assert "chat_id" in required
    # Room-less agent tool: no chat_id in schema.
    cr = await _list_tool(mcp, "band_create_chatroom")
    assert cr is not None
    assert "chat_id" not in cr.inputSchema.get("properties", {})


def test_agent_room_bound_model_accepts_room_id_alias() -> None:
    definition = TOOL_DEFINITIONS["band_send_message"]
    extended = _extend_with_chat_id(definition.input_model, None)
    v1 = extended.model_validate({"content": "hi", "mentions": ["@x"], "room_id": "r1"})
    assert v1.chat_id == "r1"
    v2 = extended.model_validate({"content": "hi", "mentions": ["@x"], "chat_id": "r2"})
    assert v2.chat_id == "r2"


def test_agent_room_bound_model_preserves_sdk_description() -> None:
    definition = TOOL_DEFINITIONS["band_send_message"]
    extended = _extend_with_chat_id(definition.input_model, None)
    assert extended.__doc__ == definition.input_model.__doc__


async def test_agent_send_event_accepts_legacy_tool_event_types() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["agent"], tools=[], agent_key="k")
    register_tools(mcp, cfg)

    t = await _list_tool(mcp, "band_send_event")
    assert t is not None
    props = t.inputSchema.get("properties", {})
    assert props["message_type"]["enum"] == [
        "tool_call",
        "tool_result",
        "thought",
        "error",
        "task",
    ]


async def test_unpinned_agent_handler_calls_get_agent_tools_with_chat_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fake AgentTools method
    fake_agent_tools = MagicMock()
    fake_agent_tools.participants = []
    fake_agent_tools.get_participants = AsyncMock(return_value=[])
    fake_agent_tools.send_message = AsyncMock(return_value={"ok": True})

    get_agent_tools_spy = MagicMock(return_value=fake_agent_tools)

    monkeypatch.setattr(registrar, "get_agent_tools", get_agent_tools_spy)
    monkeypatch.setattr(registrar, "get_human_tools", MagicMock())

    definition = TOOL_DEFINITIONS["band_send_message"]
    extended = _extend_with_chat_id(definition.input_model, None)

    handler = make_handler(
        tool_name=definition.name,
        surface="agent",
        method_name=definition.method_name,
        input_model=extended,
        pinned_room_id=None,
        is_agent_room_bound=True,
        is_human_room_bound=False,
    )

    ctx = MagicMock()
    out = await handler(ctx=ctx, content="hello", mentions=["@bob"], chat_id="r1")

    get_agent_tools_spy.assert_called_once_with(ctx, "r1")
    # chat_id must NOT reach the AgentTools method call — AgentTools is
    # constructor-scoped and its methods don't take chat_id. The MCP layer
    # refreshes participants when the cached SDK instance has no participant
    # snapshot yet so first-call mention resolution can work.
    fake_agent_tools.get_participants.assert_awaited_once_with()
    fake_agent_tools.send_message.assert_awaited_once()
    call_kwargs = fake_agent_tools.send_message.await_args.kwargs
    assert "chat_id" not in call_kwargs
    assert call_kwargs == {"content": "hello", "mentions": ["@bob"]}
    assert "ok" in out


async def test_unpinned_agent_handler_accepts_room_id_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent_tools = MagicMock()
    fake_agent_tools.send_message = AsyncMock(return_value={"ok": True})
    get_agent_tools_spy = MagicMock(return_value=fake_agent_tools)
    monkeypatch.setattr(registrar, "get_agent_tools", get_agent_tools_spy)
    monkeypatch.setattr(registrar, "get_human_tools", MagicMock())

    definition = TOOL_DEFINITIONS["band_send_message"]
    extended = _extend_with_chat_id(definition.input_model, None)

    # Exercise the dispatch path directly: validation via AliasChoices
    # resolves ``room_id`` to ``chat_id`` inside the extended input model.
    from band_mcp.tools.registrar import _invoke

    out = await _invoke(
        surface="agent",
        tool_name=definition.name,
        method_name=definition.method_name,
        input_model=extended,
        pinned_room_id=None,
        is_agent_room_bound=True,
        is_human_room_bound=False,
        ctx=MagicMock(),
        kwargs={"content": "hi", "mentions": ["@x"], "room_id": "r_alias"},
    )
    get_agent_tools_spy.assert_called_once()
    assert get_agent_tools_spy.call_args.args[1] == "r_alias"
    assert "ok" in out


async def test_validation_errors_report_fields() -> None:
    definition = TOOL_DEFINITIONS["band_send_message"]
    extended = _extend_with_chat_id(definition.input_model, None)

    from band_mcp.tools.registrar import _invoke

    with pytest.raises(ValueError, match="Invalid arguments") as exc_info:
        await _invoke(
            surface="agent",
            tool_name=definition.name,
            method_name=definition.method_name,
            input_model=extended,
            pinned_room_id=None,
            is_agent_room_bound=True,
            is_human_room_bound=False,
            ctx=MagicMock(),
            kwargs={"mentions": ["@x"], "room_id": "r_alias"},
        )

    assert "content" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Pinned agent handler: schema + dispatch
# ---------------------------------------------------------------------------


async def test_pinned_agent_schema_hides_chat_id() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["agent"], tools=[], agent_key="k", room_id="r_pinned")
    register_tools(mcp, cfg)

    t = await _list_tool(mcp, "band_send_message")
    assert t is not None
    props = t.inputSchema.get("properties", {})
    assert "chat_id" not in props
    assert "room_id" not in props


async def test_pinned_agent_handler_injects_room_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent_tools = MagicMock()
    fake_agent_tools.send_message = AsyncMock(return_value={"ok": True})
    get_agent_tools_spy = MagicMock(return_value=fake_agent_tools)
    monkeypatch.setattr(registrar, "get_agent_tools", get_agent_tools_spy)
    monkeypatch.setattr(registrar, "get_human_tools", MagicMock())

    definition = TOOL_DEFINITIONS["band_send_message"]
    pinned = _extend_with_chat_id(definition.input_model, "r_pinned")
    handler = make_handler(
        tool_name=definition.name,
        surface="agent",
        method_name=definition.method_name,
        input_model=pinned,
        pinned_room_id="r_pinned",
        is_agent_room_bound=True,
        is_human_room_bound=False,
    )

    ctx = MagicMock()
    await handler(ctx=ctx, content="hi", mentions=["@x"])

    get_agent_tools_spy.assert_called_once_with(ctx, "r_pinned")


async def test_pinned_agent_handler_overrides_caller_chat_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent_tools = MagicMock()
    fake_agent_tools.send_message = AsyncMock(return_value={"ok": True})
    get_agent_tools_spy = MagicMock(return_value=fake_agent_tools)
    monkeypatch.setattr(registrar, "get_agent_tools", get_agent_tools_spy)
    monkeypatch.setattr(registrar, "get_human_tools", MagicMock())

    definition = TOOL_DEFINITIONS["band_send_message"]
    pinned = _extend_with_chat_id(definition.input_model, "r_pinned")

    from band_mcp.tools.registrar import _invoke

    await _invoke(
        surface="agent",
        tool_name=definition.name,
        method_name=definition.method_name,
        input_model=pinned,
        pinned_room_id="r_pinned",
        is_agent_room_bound=True,
        is_human_room_bound=False,
        ctx=MagicMock(),
        kwargs={"content": "hi", "mentions": ["@x"], "chat_id": "r_user"},
    )

    get_agent_tools_spy.assert_called_once()
    assert get_agent_tools_spy.call_args.args[1] == "r_pinned"


# ---------------------------------------------------------------------------
# Human room-bound handler
# ---------------------------------------------------------------------------


async def test_unpinned_human_room_bound_advertises_chat_id() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["human"], tools=[], user_key="k")
    register_tools(mcp, cfg)

    t = await _list_tool(mcp, "band_send_my_chat_message")
    assert t is not None
    props = t.inputSchema.get("properties", {})
    required = t.inputSchema.get("required", [])
    assert "chat_id" in props
    assert "chat_id" in required


async def test_unpinned_human_handler_passes_chat_id_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_human_tools = MagicMock()
    fake_human_tools.send_my_chat_message = AsyncMock(return_value={"ok": True})

    monkeypatch.setattr(
        registrar, "get_human_tools", MagicMock(return_value=fake_human_tools)
    )
    monkeypatch.setattr(registrar, "get_agent_tools", MagicMock())

    definition = TOOL_DEFINITIONS["band_send_my_chat_message"]
    handler = make_handler(
        tool_name=definition.name,
        surface="human",
        method_name=definition.method_name,
        input_model=definition.input_model,
        pinned_room_id=None,
        is_agent_room_bound=False,
        is_human_room_bound=True,
    )

    ctx = MagicMock()
    await handler(ctx=ctx, chat_id="r1", content="hi", recipients="@bob")

    call_kwargs = fake_human_tools.send_my_chat_message.await_args.kwargs
    assert call_kwargs["chat_id"] == "r1"
    assert call_kwargs["content"] == "hi"


async def test_pinned_human_handler_injects_chat_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_human_tools = MagicMock()
    fake_human_tools.send_my_chat_message = AsyncMock(return_value={"ok": True})

    monkeypatch.setattr(
        registrar, "get_human_tools", MagicMock(return_value=fake_human_tools)
    )
    monkeypatch.setattr(registrar, "get_agent_tools", MagicMock())

    definition = TOOL_DEFINITIONS["band_send_my_chat_message"]
    pinned = _pin_existing_chat_id(definition.input_model, "r_pin")
    handler = make_handler(
        tool_name=definition.name,
        surface="human",
        method_name=definition.method_name,
        input_model=pinned,
        pinned_room_id="r_pin",
        is_agent_room_bound=False,
        is_human_room_bound=True,
    )

    ctx = MagicMock()
    await handler(ctx=ctx, content="hi", recipients="@x")

    call_kwargs = fake_human_tools.send_my_chat_message.await_args.kwargs
    assert call_kwargs["chat_id"] == "r_pin"


async def test_pinned_human_room_bound_schema_hides_chat_id() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["human"], tools=[], user_key="k", room_id="r_pin")
    register_tools(mcp, cfg)

    t = await _list_tool(mcp, "band_send_my_chat_message")
    assert t is not None
    assert "chat_id" not in t.inputSchema.get("properties", {})


# ---------------------------------------------------------------------------
# Room-less tools stay unchanged regardless of pin state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pin", [None, "r_pin"])
@pytest.mark.parametrize(
    "tool_name",
    ["band_list_my_chats", "band_get_my_profile"],
)
async def test_room_less_human_tools_schema_unchanged_by_pin(
    pin: str | None, tool_name: str
) -> None:
    mcp = FastMCP(name="t")
    cfg = Config(scope=["human"], tools=[], user_key="k", room_id=pin)
    register_tools(mcp, cfg)

    t = await _list_tool(mcp, tool_name)
    assert t is not None
    props = t.inputSchema.get("properties", {})
    # These tools have no chat_id in their underlying input model.
    assert "chat_id" not in props


async def test_room_less_list_my_contacts_unchanged_by_pin() -> None:
    mcp = FastMCP(name="t")
    cfg = Config(
        scope=["human"],
        tools=["contacts"],
        user_key="k",
        room_id="r_pin",
    )
    register_tools(mcp, cfg)

    t = await _list_tool(mcp, "band_list_my_contacts")
    assert t is not None
    assert "chat_id" not in t.inputSchema.get("properties", {})


# ---------------------------------------------------------------------------
# AgentTools cache is preserved across invocations
# ---------------------------------------------------------------------------


async def test_agent_tools_cache_is_not_reset_between_invocations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent_tools = MagicMock()
    fake_agent_tools.get_participants = AsyncMock(return_value=[])
    fake_agent_tools.send_message = AsyncMock(return_value={"ok": True})

    get_agent_tools_spy = MagicMock(return_value=fake_agent_tools)
    monkeypatch.setattr(registrar, "get_agent_tools", get_agent_tools_spy)
    monkeypatch.setattr(registrar, "get_human_tools", MagicMock())

    definition = TOOL_DEFINITIONS["band_send_message"]
    extended = _extend_with_chat_id(definition.input_model, None)
    handler = make_handler(
        tool_name=definition.name,
        surface="agent",
        method_name=definition.method_name,
        input_model=extended,
        pinned_room_id=None,
        is_agent_room_bound=True,
        is_human_room_bound=False,
    )

    ctx = MagicMock()
    await handler(ctx=ctx, content="a", mentions=["@x"], chat_id="r1")
    await handler(ctx=ctx, content="b", mentions=["@x"], chat_id="r1")

    assert get_agent_tools_spy.call_count == 2
    assert fake_agent_tools.get_participants.await_count == 2
    assert not hasattr(registrar, "reset_agent_tools_cache")


async def test_agent_tools_cache_entry_is_discarded_when_participant_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent_tools = MagicMock()
    fake_agent_tools.participants = []
    fake_agent_tools.get_participants = AsyncMock(side_effect=PermissionError("denied"))
    fake_agent_tools.send_message = AsyncMock(return_value={"ok": True})

    get_agent_tools_spy = MagicMock(return_value=fake_agent_tools)
    discard_spy = MagicMock()
    monkeypatch.setattr(registrar, "get_agent_tools", get_agent_tools_spy)
    monkeypatch.setattr(registrar, "discard_agent_tools", discard_spy)
    monkeypatch.setattr(registrar, "get_human_tools", MagicMock())

    definition = TOOL_DEFINITIONS["band_send_message"]
    extended = _extend_with_chat_id(definition.input_model, None)
    handler = make_handler(
        tool_name=definition.name,
        surface="agent",
        method_name=definition.method_name,
        input_model=extended,
        pinned_room_id=None,
        is_agent_room_bound=True,
        is_human_room_bound=False,
    )

    ctx = MagicMock()
    with pytest.raises(PermissionError, match="denied"):
        await handler(ctx=ctx, content="a", mentions=["@x"], chat_id="bad_room")

    fake_agent_tools.send_message.assert_not_called()
    discard_spy.assert_called_once_with(ctx, "bad_room", fake_agent_tools)


# ---------------------------------------------------------------------------
# Old handler coexistence — legacy handwritten handler names do not collide
# ---------------------------------------------------------------------------


def test_new_tool_names_are_prefixed_no_collision_with_legacy() -> None:
    # SDK names are all prefixed. Legacy handwritten handler names are not
    # (e.g. ``list_my_contacts``, ``get_my_chat``). Any collision would have
    # FastMCP warn & keep the first registration.
    mcp = FastMCP(name="t")
    cfg = Config(
        scope=["agent", "human"],
        tools=["contacts", "memory"],
        agent_key="a",
        user_key="u",
    )
    register_tools(mcp, cfg)

    for name in _registered_names(mcp):
        assert name.startswith("band_"), name
