from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from thenvoi.core.types import AdapterFeatures, Capability
from thenvoi.integrations.parlant import tools as parlant_tools


class _FakeParlantAgent:
    def __init__(self) -> None:
        self.guidelines: list[dict[str, Any]] = []

    async def create_guideline(
        self,
        *,
        condition: str,
        action: str,
        tools: list[Any],
    ) -> None:
        self.guidelines.append(
            {"condition": condition, "action": action, "tools": tools}
        )


class _FakeServer:
    created_agents: list[dict[str, str]] = []
    latest_agent: _FakeParlantAgent | None = None

    def __init__(self, *, nlp_service: str) -> None:
        self.nlp_service = nlp_service

    async def __aenter__(self) -> _FakeServer:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def create_agent(self, *, name: str, description: str) -> _FakeParlantAgent:
        agent = _FakeParlantAgent()
        type(self).created_agents.append({"name": name, "description": description})
        type(self).latest_agent = agent
        return agent


class _FakeRuntimeAgent:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.ran = False

    async def run(self) -> None:
        self.ran = True


@pytest.fixture()
def run_agent_module() -> types.ModuleType:
    module_path = Path(__file__).parents[2] / "examples" / "run_agent.py"
    spec = importlib.util.spec_from_file_location("run_agent_example", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_run_parlant_agent_uses_current_adapter_api(
    run_agent_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parlant_sdk = types.ModuleType("parlant.sdk")
    setattr(parlant_sdk, "NLPServices", types.SimpleNamespace(openai="openai"))
    setattr(parlant_sdk, "Server", _FakeServer)
    parlant_pkg = types.ModuleType("parlant")
    setattr(parlant_pkg, "sdk", parlant_sdk)
    monkeypatch.setitem(sys.modules, "parlant", parlant_pkg)
    monkeypatch.setitem(sys.modules, "parlant.sdk", parlant_sdk)

    captured_features: list[AdapterFeatures | None] = []
    fake_tools = [object()]

    def fake_create_parlant_tools(
        features: AdapterFeatures | None = None,
    ) -> list[Any]:
        captured_features.append(features)
        return fake_tools

    monkeypatch.setattr(
        parlant_tools,
        "create_parlant_tools",
        fake_create_parlant_tools,
    )

    created_agents: list[_FakeRuntimeAgent] = []

    class FakeAgentFactory:
        @staticmethod
        def create(**kwargs: Any) -> _FakeRuntimeAgent:
            agent = _FakeRuntimeAgent(**kwargs)
            created_agents.append(agent)
            return agent

    monkeypatch.setattr(run_agent_module, "Agent", FakeAgentFactory)
    _FakeServer.created_agents = []
    _FakeServer.latest_agent = None

    await run_agent_module.run_parlant_agent(
        agent_id="agent-id",
        api_key="api-key",
        rest_url="https://rest.example",
        ws_url="wss://ws.example",
        model=None,
        custom_section="Prefer concise replies.",
        enable_streaming=False,
        logger=logging.getLogger("test_run_parlant_agent"),
    )

    assert captured_features == [AdapterFeatures(capabilities={Capability.CONTACTS})]
    assert _FakeServer.created_agents == [
        {
            "name": "Thenvoi Parlant",
            "description": run_agent_module.PARLANT_DEFAULT_DESCRIPTION
            + "\n\nPrefer concise replies.",
        }
    ]
    assert _FakeServer.latest_agent is not None
    assert len(_FakeServer.latest_agent.guidelines) == len(
        run_agent_module.PARLANT_GUIDELINES
    )
    assert all(
        guideline["tools"] is fake_tools
        for guideline in _FakeServer.latest_agent.guidelines
    )
    assert len(created_agents) == 1
    assert created_agents[0].ran is True
    assert created_agents[0].kwargs["agent_id"] == "agent-id"
    assert created_agents[0].kwargs["api_key"] == "api-key"
    assert created_agents[0].kwargs["rest_url"] == "https://rest.example"
    assert created_agents[0].kwargs["ws_url"] == "wss://ws.example"
