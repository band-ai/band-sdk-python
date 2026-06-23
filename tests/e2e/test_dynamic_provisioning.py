"""Unit tests for the dynamic companion-agent provisioner (no network)."""

from __future__ import annotations

import os
from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.e2e.dynamic_provisioning import (
    AGENT_NAME_PREFIX,
    DynamicProvisioner,
    provision,
)


def _mock_client() -> MagicMock:
    client = MagicMock()
    counter = {"n": 0}

    async def _register(*, agent: object) -> SimpleNamespace:
        counter["n"] += 1
        n = counter["n"]
        return SimpleNamespace(
            data=SimpleNamespace(
                agent=SimpleNamespace(
                    id=f"id-{n}",
                    name=agent.name,  # type: ignore[attr-defined]
                    description=agent.description,  # type: ignore[attr-defined]
                ),
                credentials=SimpleNamespace(api_key=f"key-{n}"),
            )
        )

    client.human_api_agents.register_my_agent = AsyncMock(side_effect=_register)
    client.human_api_agents.delete_my_agent = AsyncMock()
    client.human_api_agents.list_my_agents = AsyncMock(
        return_value=SimpleNamespace(
            data=[
                SimpleNamespace(id="orphan-1", name=f"{AGENT_NAME_PREFIX}echo-old"),
                SimpleNamespace(id="real-1", name="prod-agent"),
            ]
        )
    )
    client.agent_api_identity.get_agent_me = AsyncMock(
        return_value=SimpleNamespace(
            data=SimpleNamespace(handle="owner/some", name="n")
        )
    )
    return client


@pytest.fixture
def isolate_env() -> Generator[None, None, None]:
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


async def test_mint_returns_credentials_and_tracks_id() -> None:
    client = _mock_client()
    with patch("tests.e2e.dynamic_provisioning.AsyncRestClient", return_value=client):
        prov = DynamicProvisioner(
            user_api_key="band_u_x", base_url="https://x", run_id="RUN"
        )
        minted = await prov.mint_agent("echo", "desc")

    assert minted.agent_id == "id-1"
    assert minted.api_key == "key-1"
    assert minted.name == f"{AGENT_NAME_PREFIX}echo-RUN"
    assert minted.handle == "owner/some"  # lstripped, no leading @
    assert prov._minted_ids == ["id-1"]


async def test_mint_name_carries_prefix_and_run_id() -> None:
    client = _mock_client()
    with patch("tests.e2e.dynamic_provisioning.AsyncRestClient", return_value=client):
        prov = DynamicProvisioner(
            user_api_key="band_u_x", base_url="https://x", run_id="abc123"
        )
        await prov.mint_agent("l3-calc", "desc")

    sent = client.human_api_agents.register_my_agent.call_args.kwargs["agent"]
    assert sent.name == "e2e-l3-calc-abc123"


async def test_provision_populates_expected_env(isolate_env: None) -> None:
    client = _mock_client()
    with patch("tests.e2e.dynamic_provisioning.AsyncRestClient", return_value=client):
        prov = DynamicProvisioner(
            user_api_key="band_u_x", base_url="https://x", run_id="RUN"
        )
        await provision(prov)

    # Echo: id/api_key/name/handle, no description.
    assert os.environ["E2E_ECHO_AGENT_ID"].startswith("id-")
    assert os.environ["E2E_ECHO_AGENT_API_KEY"].startswith("key-")
    assert os.environ["E2E_ECHO_AGENT_NAME"] == "e2e-echo-RUN"
    assert os.environ["E2E_ECHO_AGENT_HANDLE"] == "@owner/some"
    assert "E2E_ECHO_AGENT_DESCRIPTION" not in os.environ

    # L3 calc: includes the functional description + handle.
    assert os.environ["E2E_L3_CALC_AGENT_ID"].startswith("id-")
    assert os.environ["E2E_L3_CALC_AGENT_HANDLE"] == "@owner/some"
    assert "arithmetic" in os.environ["E2E_L3_CALC_AGENT_DESCRIPTION"]

    # L4 per-framework: id/api_key/name only (consumed by _adapter_credentials_from_env).
    assert os.environ["E2E_LANGGRAPH_AGENT_ID"].startswith("id-")
    assert os.environ["E2E_LANGGRAPH_AGENT_API_KEY"].startswith("key-")
    assert os.environ["E2E_LANGGRAPH_AGENT_NAME"] == "e2e-l4-langgraph-RUN"
    assert "E2E_LANGGRAPH_AGENT_HANDLE" not in os.environ
    assert "E2E_PYDANTIC_AI_AGENT_ID" in os.environ
    assert "E2E_CLAUDE_SDK_AGENT_ID" in os.environ


async def test_provision_sweeps_only_prefixed_orphans(isolate_env: None) -> None:
    client = _mock_client()
    with patch("tests.e2e.dynamic_provisioning.AsyncRestClient", return_value=client):
        prov = DynamicProvisioner(
            user_api_key="band_u_x", base_url="https://x", run_id="RUN"
        )
        await provision(prov)

    deleted_ids = {
        call.kwargs["id"]
        for call in client.human_api_agents.delete_my_agent.call_args_list
    }
    assert "orphan-1" in deleted_ids  # e2e-* orphan reaped
    assert "real-1" not in deleted_ids  # non-prefixed agent untouched


async def test_teardown_deletes_each_minted() -> None:
    client = _mock_client()
    with patch("tests.e2e.dynamic_provisioning.AsyncRestClient", return_value=client):
        prov = DynamicProvisioner(
            user_api_key="band_u_x", base_url="https://x", run_id="RUN"
        )
        await prov.mint_agent("echo", "d")
        await prov.mint_agent("l3-calc", "d")
        client.human_api_agents.delete_my_agent.reset_mock()
        await prov.teardown()

    deleted = {
        call.kwargs["id"]
        for call in client.human_api_agents.delete_my_agent.call_args_list
    }
    assert deleted == {"id-1", "id-2"}
    assert prov._minted_ids == []
