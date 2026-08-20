"""Real protocol-level exercise of ``FakeHumanTools``.

Registers the human surface on a real ``FastMCP`` instance (via the engine +
the CLI's ``standalone_spec``) and dispatches through it, proving the fake is
a faithful stand-in for ``HumanTools`` -- not just that it type-checks. Real
MCP protocol round-trips; the REST boundary is the only fake.

``StandaloneResolver`` takes ``human_tools`` as a constructor argument
directly, so the fake plugs in with no patching at all.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from band.integrations.mcp.engine import build_engine
from band_mcp.config import Config
from band_mcp.server import standalone_spec
from band_mcp.shared import StandaloneResolver
from tests.mcp.conftest import FakeHumanTools


async def _call(
    mcp: FastMCP, human_tools: FakeHumanTools, name: str, **kwargs: object
) -> Any:
    """Dispatch through the real engine handler and parse its JSON string.

    Matches the engine's wire shape (``_serialize()``): a dict/list result
    round-trips through ``json.dumps``, while a raw string result (the
    "Error: ..." handler convention) passes through unparsed.
    """
    raw = await mcp._tool_manager.call_tool(name, kwargs)
    assert isinstance(raw, str)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


@pytest.fixture
def build_human_mcp():
    """Factory: each test seeds its own FakeHumanTools, so each needs its own
    engine bound to that specific instance -- the resolver (and its
    human_tools) is baked in at build time now, not resolved per call."""

    def _build(human_tools: FakeHumanTools) -> FastMCP:
        cfg = Config(scope=["human"], tools=["contacts", "memory"], user_key="u")
        resolver = StandaloneResolver(human_tools=human_tools)
        return build_engine(standalone_spec(cfg, resolver))

    return _build


async def test_create_and_get_chat_room_round_trip(build_human_mcp) -> None:
    fake = FakeHumanTools()
    mcp = build_human_mcp(fake)

    created = await _call(mcp, fake, "band_create_my_chat_room")
    chat_id = created["id"]

    fetched = await _call(mcp, fake, "band_get_my_chat_room", chat_id=chat_id)
    assert fetched["id"] == chat_id


async def test_send_my_chat_message_dispatches_to_known_participant(
    build_human_mcp,
) -> None:
    fake = FakeHumanTools(
        chats=[{"id": "chat-1"}],
        chat_participants={"chat-1": [{"id": "p-1", "name": "Alice"}]},
    )
    mcp = build_human_mcp(fake)

    result = await _call(
        mcp,
        fake,
        "band_send_my_chat_message",
        chat_id="chat-1",
        content="hi",
        recipients="Alice",
    )

    assert result["id"] == "msg-0"
    assert fake.messages_sent == [
        {
            "id": "msg-0",
            "chat_id": "chat-1",
            "content": "hi",
            "recipients": ["alice"],
        }
    ]


async def test_send_my_chat_message_reports_unknown_recipient(
    build_human_mcp,
) -> None:
    fake = FakeHumanTools(
        chats=[{"id": "chat-1"}],
        chat_participants={"chat-1": [{"id": "p-1", "name": "Alice"}]},
    )
    mcp = build_human_mcp(fake)

    result = await _call(
        mcp,
        fake,
        "band_send_my_chat_message",
        chat_id="chat-1",
        content="hi",
        recipients="Bob",
    )

    assert result == "Error: Not found: bob. Available: alice"
    assert fake.messages_sent == []


async def test_get_my_profile_and_update(build_human_mcp) -> None:
    fake = FakeHumanTools(
        profile={"id": "u1", "first_name": "Old", "last_name": "Name"}
    )
    mcp = build_human_mcp(fake)

    profile = await _call(mcp, fake, "band_get_my_profile")
    assert profile["first_name"] == "Old"

    updated = await _call(mcp, fake, "band_update_my_profile", first_name="New")
    assert updated["first_name"] == "New"
    assert updated["last_name"] == "Name"


async def test_list_my_contacts_and_resolve_handle(build_human_mcp) -> None:
    fake = FakeHumanTools(contacts=[{"id": "c1", "handle": "@alice", "name": "Alice"}])
    mcp = build_human_mcp(fake)

    listed = await _call(mcp, fake, "band_list_my_contacts")
    assert listed["data"] == [{"id": "c1", "handle": "@alice", "name": "Alice"}]

    resolved = await _call(mcp, fake, "band_resolve_handle", handle="@alice")
    assert resolved["id"] == "c1"


async def test_memory_lifecycle_supersede_and_delete(build_human_mcp) -> None:
    fake = FakeHumanTools(
        memories=[{"id": "m1", "content": "note", "status": "active"}]
    )
    mcp = build_human_mcp(fake)

    listed = await _call(mcp, fake, "band_list_user_memories")
    assert listed["data"][0]["id"] == "m1"

    superseded = await _call(mcp, fake, "band_supersede_user_memory", memory_id="m1")
    assert superseded["status"] == "superseded"

    deleted = await _call(mcp, fake, "band_delete_user_memory", memory_id="m1")
    assert deleted == {"deleted": True, "id": "m1"}
    assert fake.memories == []
