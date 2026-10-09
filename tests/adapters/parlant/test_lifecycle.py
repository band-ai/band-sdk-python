"""ParlantAdapter startup and shutdown: owned vs borrowed server and agent."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from band.adapters.parlant import ParlantAdapter, ParlantAdapterConfig
from band.adapters.parlant.adapter import (
    NOT_INITIALIZED_ERROR,
    UNREACHABLE_CUSTOM_TOOLS_WARNING,
)
from tests.adapters.parlant.helpers import BAND_DESCRIPTION, BAND_NAME, LOOKUP


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

    await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    mock_parlant_server.create_agent.assert_awaited_once_with(
        name=BAND_NAME, description=f"{BAND_DESCRIPTION}\n\nBe helpful."
    )


async def test_system_prompt_overrides_created_agent_description(
    mock_parlant_server,
):
    """system_prompt must fully replace the created agent's description."""
    adapter = ParlantAdapter(
        ParlantAdapterConfig(system_prompt="You are a custom assistant."),
        server=mock_parlant_server,
    )

    await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    mock_parlant_server.create_agent.assert_awaited_once_with(
        name=BAND_NAME, description="You are a custom assistant."
    )


async def test_boots_owned_server_and_creates_agent(owned_server, mock_parlant_agent):
    factory, _, server = owned_server
    adapter = ParlantAdapter(
        ParlantAdapterConfig(name="Tom", description="A cat"), nlp_service="svc"
    )

    await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    assert factory.call_count == 1
    assert factory.call_args.kwargs["nlp_service"] == "svc"
    server.create_agent.assert_awaited_once_with(name="Tom", description="A cat")
    assert adapter.server is server
    assert adapter.parlant_agent is mock_parlant_agent


async def test_name_description_default_to_band_metadata(owned_server):
    _, _, server = owned_server
    adapter = ParlantAdapter()

    await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    server.create_agent.assert_awaited_once_with(
        name=BAND_NAME, description=BAND_DESCRIPTION
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

    await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

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
    assert stub_band_tools.call_args == call(adapter.features, custom_tools=[LOOKUP])


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
        await borrowed_adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    assert [
        call.kwargs["condition"]
        for call in mock_parlant_agent.create_guideline.await_args_list
    ] == ["first", "second"]

    await borrowed_adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    assert [
        call.kwargs["condition"]
        for call in mock_parlant_agent.create_guideline.await_args_list
    ] == ["first", "second", "second", "third"]


async def test_add_guideline_after_start_raises(owned_server):
    adapter = ParlantAdapter(ParlantAdapterConfig(name="X", description="Y"))
    await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    with pytest.raises(RuntimeError, match="before the agent starts"):
        adapter.add_guideline(condition="late", action="too late")


async def test_configure_callback_receives_live_objects_and_the_tools(
    owned_server, mock_parlant_agent, stub_band_tools
):
    """configure= is where per-guideline tool picks read ``adapter.tools``."""
    _, _, server = owned_server
    built_tools = ["band-tool-entry", "lookup-entry"]
    stub_band_tools.return_value = built_tools
    seen: list[tuple] = []

    async def configure(srv, agent):
        seen.append((srv, agent, adapter.tools))

    adapter = ParlantAdapter(
        ParlantAdapterConfig(name="X", description="Y"),
        additional_tools=[LOOKUP],
        configure=configure,
    )
    await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    assert seen == [(server, mock_parlant_agent, built_tools)]


async def test_cleanup_all_closes_owned_server(owned_server):
    _, cm, _ = owned_server
    adapter = ParlantAdapter(ParlantAdapterConfig(name="X", description="Y"))
    await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    await adapter.cleanup_all()

    cm.__aexit__.assert_awaited_once()
    with pytest.raises(RuntimeError, match="not running yet"):
        _ = adapter.server


async def test_cleanup_all_leaves_borrowed_server(
    borrowed_adapter, mock_parlant_server, mock_parlant_agent, run_turn
):
    await borrowed_adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    await borrowed_adapter.cleanup_all()

    assert borrowed_adapter.server is mock_parlant_server
    assert borrowed_adapter.parlant_agent is mock_parlant_agent
    with pytest.raises(RuntimeError, match=NOT_INITIALIZED_ERROR):
        await run_turn(borrowed_adapter)


async def test_restart_with_borrowed_server_does_not_duplicate_guidelines(
    borrowed_adapter, mock_parlant_agent
):
    """A borrowed agent survives cleanup; its guidelines must not re-create."""
    borrowed_adapter.add_guideline(condition="c", action="a")

    await borrowed_adapter.on_started(BAND_NAME, BAND_DESCRIPTION)
    await borrowed_adapter.cleanup_all()
    await borrowed_adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    assert mock_parlant_agent.create_guideline.await_count == 1


async def test_restart_with_owned_server_applies_guidelines_to_fresh_agent(
    owned_server, mock_parlant_agent
):
    adapter = ParlantAdapter(ParlantAdapterConfig(name="X", description="Y"))
    adapter.add_guideline(condition="c", action="a")

    await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)
    await adapter.cleanup_all()
    await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    assert mock_parlant_agent.create_guideline.await_count == 2


async def test_on_started_failure_leaves_cleanup_to_server_context(owned_server):
    _, cm, _ = owned_server

    async def configure(srv, agent):
        raise RuntimeError("configure blew up")

    adapter = ParlantAdapter(
        ParlantAdapterConfig(name="X", description="Y"), configure=configure
    )

    with pytest.raises(RuntimeError, match="configure blew up"):
        await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    # A context manager whose __aenter__ raises owns its partial-enter cleanup;
    # calling __aexit__ again from the adapter would double-close it.
    cm.__aexit__.assert_not_awaited()
    with pytest.raises(RuntimeError, match="not running yet"):
        _ = adapter.server


async def configure_nothing(server: object, agent: object) -> None:
    """A configure= callback, the route for wiring tools per guideline."""


@pytest.mark.parametrize(
    ("adapter_kwargs", "guideline_tools", "warnings"),
    [
        ({"additional_tools": [LOOKUP]}, [], 1),
        ({"additional_tools": [LOOKUP]}, None, 0),
        ({"additional_tools": [LOOKUP], "configure": configure_nothing}, [], 0),
        ({}, [], 0),
    ],
    ids=[
        "no-guideline-keeps-default-tools",
        "guideline-keeps-default-tools",
        "configure-wires-tools",
        "no-custom-tools",
    ],
)
async def test_warns_when_no_guideline_can_reach_custom_tools(
    mock_parlant_server,
    mock_parlant_agent,
    caplog,
    adapter_kwargs,
    guideline_tools,
    warnings,
):
    adapter = ParlantAdapter(
        server=mock_parlant_server, parlant_agent=mock_parlant_agent, **adapter_kwargs
    )
    adapter.add_guideline(condition="c", action="a", tools=guideline_tools)

    with caplog.at_level(logging.WARNING, logger="band.adapters.parlant.adapter"):
        await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

    unreachable = [
        r for r in caplog.records if r.msg == UNREACHABLE_CUSTOM_TOOLS_WARNING
    ]
    assert [r.levelno for r in unreachable] == [logging.WARNING] * warnings
