"""Tests for the generic example runner."""

from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
import importlib.util
import sys

import pytest

from thenvoi.core.types import Capability, Emit
from thenvoi.runtime.types import ContactEventConfig, ContactEventStrategy


@pytest.fixture
def run_agent_module():
    """Import examples/run_agent.py as a test module."""
    module_path = str(Path(__file__).resolve().parents[1] / "examples" / "run_agent.py")
    spec = importlib.util.spec_from_file_location("example_run_agent", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["example_run_agent"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_parlant_sdk(monkeypatch):
    """Install a minimal parlant.sdk module for runner tests."""
    sdk = ModuleType("parlant.sdk")
    sdk.NLPServices = SimpleNamespace(openai=object())

    class FakeServer:
        def __init__(self, *, nlp_service):
            self.nlp_service = nlp_service
            self.created_agent = SimpleNamespace(create_guideline=AsyncMock())

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def create_agent(self, **kwargs):
            self.create_agent_kwargs = kwargs
            return self.created_agent

    sdk.Server = FakeServer

    package = ModuleType("parlant")
    package.sdk = sdk
    monkeypatch.setitem(sys.modules, "parlant", package)
    monkeypatch.setitem(sys.modules, "parlant.sdk", sdk)
    return sdk


@pytest.mark.asyncio
async def test_run_parlant_agent_enables_contacts_for_runtime_and_tools(
    run_agent_module,
    fake_parlant_sdk,
    monkeypatch,
):
    """Parlant contacts need both Agent.create contact_config and CONTACTS tools."""
    captured = {}

    class FakeAdapter:
        def __init__(self, **kwargs):
            captured["adapter_kwargs"] = kwargs

    class FakeAgent:
        @classmethod
        def create(cls, **kwargs):
            captured["agent_create_kwargs"] = kwargs
            return SimpleNamespace(run=AsyncMock())

    def fake_create_parlant_tools(features=None, *, legacy_defaults=None):
        captured["tool_features"] = features
        captured["legacy_defaults"] = legacy_defaults
        return ["tool-ref"]

    import thenvoi.adapters
    import thenvoi.integrations.parlant.tools

    monkeypatch.setattr(thenvoi.adapters, "ParlantAdapter", FakeAdapter)
    monkeypatch.setattr(
        thenvoi.integrations.parlant.tools,
        "create_parlant_tools",
        fake_create_parlant_tools,
    )
    monkeypatch.setattr(run_agent_module, "Agent", FakeAgent)

    contact_config = ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM)

    await run_agent_module.run_parlant_agent(
        agent_id="agent-id",
        api_key="api-key",
        rest_url="https://example.test",
        ws_url="wss://example.test/socket",
        model=None,
        custom_section="Be concise",
        enable_streaming=False,
        contact_config=contact_config,
        logger=SimpleNamespace(warning=lambda *args: None, info=lambda *args: None),
    )

    assert captured["agent_create_kwargs"]["contact_config"] is contact_config
    assert captured["adapter_kwargs"]["features"] is captured["tool_features"]
    assert captured["tool_features"].capabilities == frozenset({Capability.CONTACTS})
    assert captured["legacy_defaults"] is False


@pytest.mark.asyncio
async def test_run_parlant_agent_broadcast_contacts_do_not_enable_contact_tools(
    run_agent_module,
    fake_parlant_sdk,
    monkeypatch,
):
    """Broadcast-only contact mode should not grant LLM contact-management tools."""
    captured = {}

    class FakeAdapter:
        def __init__(self, **kwargs):
            captured["adapter_kwargs"] = kwargs

    class FakeAgent:
        @classmethod
        def create(cls, **kwargs):
            captured["agent_create_kwargs"] = kwargs
            return SimpleNamespace(run=AsyncMock())

    def fake_create_parlant_tools(features=None, *, legacy_defaults=None):
        captured["tool_features"] = features
        captured["legacy_defaults"] = legacy_defaults
        return ["tool-ref"]

    import thenvoi.adapters
    import thenvoi.integrations.parlant.tools

    monkeypatch.setattr(thenvoi.adapters, "ParlantAdapter", FakeAdapter)
    monkeypatch.setattr(
        thenvoi.integrations.parlant.tools,
        "create_parlant_tools",
        fake_create_parlant_tools,
    )
    monkeypatch.setattr(run_agent_module, "Agent", FakeAgent)

    contact_config = ContactEventConfig(
        strategy=ContactEventStrategy.DISABLED,
        broadcast_changes=True,
    )

    await run_agent_module.run_parlant_agent(
        agent_id="agent-id",
        api_key="api-key",
        rest_url="https://example.test",
        ws_url="wss://example.test/socket",
        model=None,
        custom_section="Be concise",
        enable_streaming=False,
        contact_config=contact_config,
        logger=SimpleNamespace(warning=lambda *args: None, info=lambda *args: None),
    )

    assert captured["agent_create_kwargs"]["contact_config"] is contact_config
    assert captured["adapter_kwargs"]["features"] is None
    assert captured["tool_features"] is None
    assert captured["legacy_defaults"] is False


@pytest.mark.asyncio
async def test_run_parlant_agent_excludes_contact_tools_without_contacts(
    run_agent_module,
    fake_parlant_sdk,
    monkeypatch,
):
    """Without contact_config the runner should not grant contact-management tools."""
    captured = {}

    class FakeAdapter:
        def __init__(self, **kwargs):
            captured["adapter_kwargs"] = kwargs

    class FakeAgent:
        @classmethod
        def create(cls, **kwargs):
            captured["agent_create_kwargs"] = kwargs
            return SimpleNamespace(run=AsyncMock())

    def fake_create_parlant_tools(features=None, *, legacy_defaults=None):
        captured["tool_features"] = features
        captured["legacy_defaults"] = legacy_defaults
        return ["tool-ref"]

    import thenvoi.adapters
    import thenvoi.integrations.parlant.tools

    monkeypatch.setattr(thenvoi.adapters, "ParlantAdapter", FakeAdapter)
    monkeypatch.setattr(
        thenvoi.integrations.parlant.tools,
        "create_parlant_tools",
        fake_create_parlant_tools,
    )
    monkeypatch.setattr(run_agent_module, "Agent", FakeAgent)

    await run_agent_module.run_parlant_agent(
        agent_id="agent-id",
        api_key="api-key",
        rest_url="https://example.test",
        ws_url="wss://example.test/socket",
        model=None,
        custom_section="Be concise",
        enable_streaming=False,
        contact_config=None,
        logger=SimpleNamespace(warning=lambda *args: None, info=lambda *args: None),
    )

    assert captured["agent_create_kwargs"].get("contact_config") is None
    assert captured["adapter_kwargs"]["features"] is None
    assert captured["tool_features"] is None
    assert captured["legacy_defaults"] is False


@pytest.mark.asyncio
async def test_run_parlant_agent_enables_execution_reporting(
    run_agent_module,
    fake_parlant_sdk,
    monkeypatch,
):
    """--streaming should enable Emit.EXECUTION for the Parlant adapter."""
    captured = {}

    class FakeAdapter:
        def __init__(self, **kwargs):
            captured["adapter_kwargs"] = kwargs

    class FakeAgent:
        @classmethod
        def create(cls, **kwargs):
            captured["agent_create_kwargs"] = kwargs
            return SimpleNamespace(run=AsyncMock())

    def fake_create_parlant_tools(features=None, *, legacy_defaults=None):
        captured["tool_features"] = features
        captured["legacy_defaults"] = legacy_defaults
        return ["tool-ref"]

    import thenvoi.adapters
    import thenvoi.integrations.parlant.tools

    monkeypatch.setattr(thenvoi.adapters, "ParlantAdapter", FakeAdapter)
    monkeypatch.setattr(
        thenvoi.integrations.parlant.tools,
        "create_parlant_tools",
        fake_create_parlant_tools,
    )
    monkeypatch.setattr(run_agent_module, "Agent", FakeAgent)

    await run_agent_module.run_parlant_agent(
        agent_id="agent-id",
        api_key="api-key",
        rest_url="https://example.test",
        ws_url="wss://example.test/socket",
        model=None,
        custom_section="Be concise",
        enable_streaming=True,
        contact_config=None,
        logger=SimpleNamespace(warning=lambda *args: None, info=lambda *args: None),
    )

    features = captured["adapter_kwargs"]["features"]
    assert captured["tool_features"] is features
    assert features.emit == frozenset({Emit.EXECUTION})
    assert features.capabilities == frozenset()
    assert captured["legacy_defaults"] is False
