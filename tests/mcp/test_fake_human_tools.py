"""Real protocol-level exercise of ``FakeHumanTools`` (INT-1096 step 7).

Registers the human surface on a real ``FastMCP`` instance and dispatches
through it exactly as the registrar would, proving the fake is a faithful
stand-in for ``HumanTools`` -- not just that it type-checks. Governing rule
from the plan's testing-toolkit section: real MCP protocol round-trips, the
REST boundary is the only fake.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

from band_mcp.config import Config
from band_mcp.tools import registrar
from band_mcp.tools.registrar import register_tools
from tests.mcp.conftest import FakeHumanTools


def _ctx_for(human_tools: FakeHumanTools) -> SimpleNamespace:
    app_ctx = SimpleNamespace(human_tools=human_tools)
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app_ctx))


@pytest.fixture(autouse=True)
def _route_human_tools_to_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        registrar,
        "get_human_tools",
        lambda ctx: ctx.request_context.lifespan_context.human_tools,
    )
    monkeypatch.setattr(registrar, "get_agent_tools", MagicMock())


async def _call(
    mcp: FastMCP, human_tools: FakeHumanTools, name: str, **kwargs: object
) -> Any:
    """Dispatch through the real registrar handler and parse its JSON string.

    Matches the registrar's own wire shape (``_serialize()``): a dict/list
    result round-trips through ``json.dumps``, while a raw string result
    (the "Error: ..." handler convention) passes through unparsed.
    """
    raw = await mcp._tool_manager.call_tool(name, kwargs, context=_ctx_for(human_tools))
    assert isinstance(raw, str)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


@pytest.fixture
def human_mcp() -> FastMCP:
    mcp = FastMCP(name="fake-human-tools-smoke")
    cfg = Config(scope=["human"], tools=["contacts", "memory"], user_key="u")
    register_tools(mcp, cfg)
    return mcp


async def test_create_and_get_chat_room_round_trip(human_mcp: FastMCP) -> None:
    fake = FakeHumanTools()

    created = await _call(human_mcp, fake, "band_create_my_chat_room")
    chat_id = created["id"]

    fetched = await _call(human_mcp, fake, "band_get_my_chat_room", chat_id=chat_id)
    assert fetched["id"] == chat_id


async def test_send_my_chat_message_dispatches_to_known_participant(
    human_mcp: FastMCP,
) -> None:
    fake = FakeHumanTools(
        chats=[{"id": "chat-1"}],
        chat_participants={"chat-1": [{"id": "p-1", "name": "Alice"}]},
    )

    result = await _call(
        human_mcp,
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
    human_mcp: FastMCP,
) -> None:
    fake = FakeHumanTools(
        chats=[{"id": "chat-1"}],
        chat_participants={"chat-1": [{"id": "p-1", "name": "Alice"}]},
    )

    result = await _call(
        human_mcp,
        fake,
        "band_send_my_chat_message",
        chat_id="chat-1",
        content="hi",
        recipients="Bob",
    )

    assert result == "Error: Not found: bob. Available: alice"
    assert fake.messages_sent == []


async def test_get_my_profile_and_update(human_mcp: FastMCP) -> None:
    fake = FakeHumanTools(
        profile={"id": "u1", "first_name": "Old", "last_name": "Name"}
    )

    profile = await _call(human_mcp, fake, "band_get_my_profile")
    assert profile["first_name"] == "Old"

    updated = await _call(human_mcp, fake, "band_update_my_profile", first_name="New")
    assert updated["first_name"] == "New"
    assert updated["last_name"] == "Name"


async def test_list_my_contacts_and_resolve_handle(human_mcp: FastMCP) -> None:
    fake = FakeHumanTools(contacts=[{"id": "c1", "handle": "@alice", "name": "Alice"}])

    listed = await _call(human_mcp, fake, "band_list_my_contacts")
    assert listed["data"] == [{"id": "c1", "handle": "@alice", "name": "Alice"}]

    resolved = await _call(human_mcp, fake, "band_resolve_handle", handle="@alice")
    assert resolved["id"] == "c1"


async def test_memory_lifecycle_supersede_and_delete(human_mcp: FastMCP) -> None:
    fake = FakeHumanTools(
        memories=[{"id": "m1", "content": "note", "status": "active"}]
    )

    listed = await _call(human_mcp, fake, "band_list_user_memories")
    assert listed["data"][0]["id"] == "m1"

    superseded = await _call(
        human_mcp, fake, "band_supersede_user_memory", memory_id="m1"
    )
    assert superseded["status"] == "superseded"

    deleted = await _call(human_mcp, fake, "band_delete_user_memory", memory_id="m1")
    assert deleted == {"deleted": True, "id": "m1"}
    assert fake.memories == []
