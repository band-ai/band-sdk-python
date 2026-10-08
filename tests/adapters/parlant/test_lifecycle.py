"""ParlantAdapter startup and shutdown: owned vs borrowed server and agent."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from band.adapters.parlant import ParlantAdapter, ParlantAdapterConfig


@pytest.fixture
def owned_server(
    mock_parlant_server: MagicMock, mock_parlant_agent: MagicMock
) -> Iterator[tuple[MagicMock, MagicMock, MagicMock]]:
    """Patch running_parlant_server with a fake CM yielding the mock server."""
    mock_parlant_server.create_agent = AsyncMock(return_value=mock_parlant_agent)
    cm = MagicMock()

    async def enter():
        setup = factory.call_args.kwargs["setup"]
        await setup(mock_parlant_server)
        return mock_parlant_server

    cm.__aenter__ = AsyncMock(side_effect=enter)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "band.adapters.parlant.lifecycle.running_parlant_server", return_value=cm
    ) as factory:
        yield factory, cm, mock_parlant_server


class LookupInput(BaseModel):
    """Look a code up."""

    code: str


async def lookup(args: LookupInput) -> str:
    return args.code


LOOKUP = (LookupInput, lookup)


@pytest.fixture
def borrowed_adapter(mock_parlant_server, mock_parlant_agent) -> ParlantAdapter:
    return ParlantAdapter(server=mock_parlant_server, parlant_agent=mock_parlant_agent)


async def test_custom_section_appended_to_created_agent_description(
    mock_parlant_server,
):
    """custom_section must reach the created Parlant agent's description."""
    adapter = ParlantAdapter(
        ParlantAdapterConfig(custom_section="Be helpful."), server=mock_parlant_server
    )

    await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

    mock_parlant_server.create_agent.assert_awaited_once_with(
        name="TestBot", description="A test bot\n\nBe helpful."
    )


async def test_system_prompt_overrides_created_agent_description(
    mock_parlant_server,
):
    """system_prompt must fully replace the created agent's description."""
    adapter = ParlantAdapter(
        ParlantAdapterConfig(system_prompt="You are a custom assistant."),
        server=mock_parlant_server,
    )

    await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

    mock_parlant_server.create_agent.assert_awaited_once_with(
        name="TestBot", description="You are a custom assistant."
    )


@pytest.mark.usefixtures("parlant_sessions")
async def test_turns_use_the_servers_application(
    start_adapter, mock_app, sample_message, mock_tools
):
    """The Application the turn talks to comes from the server's container."""
    adapter = await start_adapter()

    await adapter.on_message(
        msg=sample_message,
        tools=mock_tools,
        history=[],
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-123",
    )

    mock_app.sessions.create_customer_message.assert_awaited_once()


async def test_boots_owned_server_and_creates_agent(owned_server, mock_parlant_agent):
    factory, cm, server = owned_server
    adapter = ParlantAdapter(
        ParlantAdapterConfig(name="Tom", description="A cat"), nlp_service="svc"
    )

    await adapter.on_started("BandName", "Band description")

    assert factory.call_count == 1
    assert factory.call_args.kwargs["nlp_service"] == "svc"
    assert callable(factory.call_args.kwargs["setup"])
    cm.__aenter__.assert_awaited_once()
    server.create_agent.assert_awaited_once_with(name="Tom", description="A cat")
    assert adapter.server is server
    assert adapter.parlant_agent is mock_parlant_agent


async def test_name_description_default_to_band_metadata(owned_server):
    _, _, server = owned_server
    adapter = ParlantAdapter()

    await adapter.on_started("BandName", "Band description")

    server.create_agent.assert_awaited_once_with(
        name="BandName", description="Band description"
    )


async def test_applies_deferred_guidelines_with_band_tools_default(
    owned_server, mock_parlant_agent, stub_band_tools
):
    band_tools = ["band-tool-entry"]
    stub_band_tools.return_value = band_tools
    adapter = ParlantAdapter(
        ParlantAdapterConfig(name="X", description="Y"), additional_tools=[LOOKUP]
    )
    adapter.add_guideline(condition="c1", action="a1")
    adapter.add_guideline(condition="c2", action="a2", tools=[])
    adapter.add_guideline(condition="c3", action="a3", metadata={"k": "v"})

    await adapter.on_started("BandName", "Band description")

    calls = mock_parlant_agent.create_guideline.await_args_list
    assert [c.kwargs for c in calls] == [
        {"condition": "c1", "action": "a1", "tools": band_tools},
        {"condition": "c2", "action": "a2", "tools": []},
        {
            "condition": "c3",
            "action": "a3",
            "tools": band_tools,
            "metadata": {"k": "v"},
        },
    ]
    assert stub_band_tools.call_args.kwargs == {"custom_tools": [LOOKUP]}


async def test_guideline_failure_has_no_live_siblings_and_retries_from_failure(
    borrowed_adapter, mock_parlant_agent
):
    """A failed create checkpoints earlier work and never starts later work."""
    mock_parlant_agent.create_guideline = AsyncMock(
        side_effect=[None, RuntimeError("bad guideline"), None, None]
    )
    borrowed_adapter.add_guideline(condition="first", action="done")
    borrowed_adapter.add_guideline(condition="second", action="retry")
    borrowed_adapter.add_guideline(condition="third", action="later")

    with pytest.raises(RuntimeError, match="bad guideline"):
        await borrowed_adapter.on_started("BandName", "Band description")

    assert [
        call.kwargs["condition"]
        for call in mock_parlant_agent.create_guideline.await_args_list
    ] == ["first", "second"]

    await borrowed_adapter.on_started("BandName", "Band description")

    assert [
        call.kwargs["condition"]
        for call in mock_parlant_agent.create_guideline.await_args_list
    ] == ["first", "second", "second", "third"]


async def test_add_guideline_after_start_raises(owned_server):
    adapter = ParlantAdapter(ParlantAdapterConfig(name="X", description="Y"))
    await adapter.on_started("BandName", "Band description")

    with pytest.raises(RuntimeError, match="before the agent starts"):
        adapter.add_guideline(condition="late", action="too late")


async def test_configure_callback_receives_live_objects(
    owned_server, mock_parlant_agent
):
    _, _, server = owned_server
    seen: list[tuple] = []

    async def configure(srv, agent):
        seen.append((srv, agent))

    adapter = ParlantAdapter(
        ParlantAdapterConfig(name="X", description="Y"), configure=configure
    )
    await adapter.on_started("BandName", "Band description")

    assert seen == [(server, mock_parlant_agent)]


async def test_cleanup_all_closes_owned_server(owned_server):
    _, cm, _ = owned_server
    adapter = ParlantAdapter(ParlantAdapterConfig(name="X", description="Y"))
    await adapter.on_started("BandName", "Band description")

    await adapter.cleanup_all()

    cm.__aexit__.assert_awaited_once()
    with pytest.raises(RuntimeError, match="not running yet"):
        _ = adapter.server


async def test_cleanup_all_leaves_borrowed_server(
    borrowed_adapter,
    mock_parlant_server,
    mock_parlant_agent,
    sample_message,
    mock_tools,
):
    await borrowed_adapter.on_started("BandName", "Band description")

    await borrowed_adapter.cleanup_all()

    assert borrowed_adapter.server is mock_parlant_server
    assert borrowed_adapter.parlant_agent is mock_parlant_agent
    with pytest.raises(RuntimeError, match="not initialized"):
        await borrowed_adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )


async def test_restart_with_borrowed_server_does_not_duplicate_guidelines(
    borrowed_adapter, mock_parlant_agent
):
    """A borrowed agent survives cleanup; its guidelines must not re-create."""
    borrowed_adapter.add_guideline(condition="c", action="a")

    await borrowed_adapter.on_started("BandName", "Band description")
    await borrowed_adapter.cleanup_all()
    await borrowed_adapter.on_started("BandName", "Band description")

    assert mock_parlant_agent.create_guideline.await_count == 1


async def test_restart_with_owned_server_applies_guidelines_to_fresh_agent(
    owned_server, mock_parlant_agent
):
    adapter = ParlantAdapter(ParlantAdapterConfig(name="X", description="Y"))
    adapter.add_guideline(condition="c", action="a")

    await adapter.on_started("BandName", "Band description")
    await adapter.cleanup_all()
    await adapter.on_started("BandName", "Band description")

    assert mock_parlant_agent.create_guideline.await_count == 2


async def test_on_started_failure_leaves_cleanup_to_server_context(owned_server):
    _, cm, _ = owned_server

    async def configure(srv, agent):
        raise RuntimeError("configure blew up")

    adapter = ParlantAdapter(
        ParlantAdapterConfig(name="X", description="Y"), configure=configure
    )

    with pytest.raises(RuntimeError, match="configure blew up"):
        await adapter.on_started("BandName", "Band description")

    # A context manager whose __aenter__ raises owns its partial-enter cleanup;
    # calling __aexit__ again from the adapter would double-close it.
    cm.__aexit__.assert_not_awaited()
    with pytest.raises(RuntimeError, match="not running yet"):
        _ = adapter.server


@pytest.mark.parametrize(
    ("guideline_tools", "warnings"),
    [([], 1), (None, 0)],
    ids=["no-guideline-keeps-default-tools", "guideline-keeps-default-tools"],
)
async def test_warns_when_no_guideline_can_reach_custom_tools(
    mock_parlant_server, mock_parlant_agent, caplog, guideline_tools, warnings
):
    adapter = ParlantAdapter(
        server=mock_parlant_server,
        parlant_agent=mock_parlant_agent,
        additional_tools=[LOOKUP],
    )
    adapter.add_guideline(condition="c", action="a", tools=guideline_tools)

    with caplog.at_level(logging.WARNING, logger="band.adapters.parlant.adapter"):
        await adapter.on_started("BandName", "Band description")

    unreachable = [r for r in caplog.records if "can never call" in r.getMessage()]
    assert [r.levelno for r in unreachable] == [logging.WARNING] * warnings
