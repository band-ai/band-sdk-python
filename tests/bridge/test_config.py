"""Tests for bridge configuration: AgentConfig, BridgeConfig, targets."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from bridge_core.config import (
    AgentConfig,
    AgentCoreTarget,
    BridgeConfig,
    HTTPTarget,
    ReconnectConfig,
)


class TestHTTPTarget:
    def test_accepts_http_url(self) -> None:
        t = HTTPTarget(url="http://localhost:8000/inv")
        assert t.url == "http://localhost:8000/inv"
        assert t.type == "http"

    def test_accepts_https_url(self) -> None:
        t = HTTPTarget(url="https://example.com/inv")
        assert t.url == "https://example.com/inv"

    def test_rejects_empty_url(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            HTTPTarget(url="")

    def test_rejects_url_without_scheme(self) -> None:
        with pytest.raises(ValueError, match="http://"):
            HTTPTarget(url="example.com/inv")

    def test_rejects_non_positive_timeout(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            HTTPTarget(url="https://x/y", timeout=0)


class TestAgentCoreTarget:
    def test_basic(self) -> None:
        t = AgentCoreTarget(
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/abc"
        )
        assert t.region == "us-east-1"
        assert t.type == "agentcore"

    def test_rejects_empty_arn(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            AgentCoreTarget(runtime_arn="")

    def test_rejects_empty_region(self) -> None:
        with pytest.raises(ValueError, match="region"):
            AgentCoreTarget(
                runtime_arn="arn:aws:bedrock-agentcore:us-east-1:1:runtime/a", region=""
            )


class TestAgentConfig:
    def test_http_target(self) -> None:
        a = AgentConfig(
            agent_id="aid",
            api_key="key",
            target=HTTPTarget(url="https://x/y"),
        )
        assert isinstance(a.target, HTTPTarget)

    def test_agentcore_target(self) -> None:
        a = AgentConfig(
            agent_id="aid",
            api_key="key",
            target=AgentCoreTarget(runtime_arn="arn:x", region="us-east-1"),
        )
        assert isinstance(a.target, AgentCoreTarget)

    def test_target_discriminator_from_dict(self) -> None:
        """Pydantic should resolve the discriminator from a dict ``type`` field."""
        a = AgentConfig.model_validate(
            {
                "agent_id": "aid",
                "api_key": "key",
                "target": {"type": "http", "url": "https://x/y"},
            }
        )
        assert isinstance(a.target, HTTPTarget)

    def test_rejects_empty_agent_id(self) -> None:
        with pytest.raises(ValueError, match="agent_id"):
            AgentConfig(agent_id="", api_key="k", target=HTTPTarget(url="https://x/y"))

    def test_rejects_empty_api_key(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            AgentConfig(
                agent_id="aid", api_key="", target=HTTPTarget(url="https://x/y")
            )


class TestBridgeConfig:
    def test_basic(self) -> None:
        c = BridgeConfig(
            agents=[
                AgentConfig(
                    agent_id="a", api_key="k", target=HTTPTarget(url="https://x/y")
                )
            ]
        )
        assert len(c.agents) == 1
        assert c.health_port == 8080

    def test_rejects_empty_agents(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            BridgeConfig(agents=[])

    def test_rejects_duplicate_agent_ids(self) -> None:
        agent = AgentConfig(
            agent_id="dup", api_key="k", target=HTTPTarget(url="https://x/y")
        )
        with pytest.raises(ValueError, match="Duplicate"):
            BridgeConfig(agents=[agent, agent])

    def test_rejects_invalid_port(self) -> None:
        with pytest.raises(ValueError, match="health_port"):
            BridgeConfig(
                agents=[
                    AgentConfig(
                        agent_id="a",
                        api_key="k",
                        target=HTTPTarget(url="https://x/y"),
                    )
                ],
                health_port=99999,
            )


class TestBridgeConfigFromEnv:
    def test_loads_http_agent(self) -> None:
        payload = json.dumps(
            [
                {
                    "agent_id": "a1",
                    "api_key": "k1",
                    "target": {"type": "http", "url": "https://x/y"},
                }
            ]
        )
        with patch.dict(os.environ, {"BAND_BRIDGE_AGENTS": payload}, clear=False):
            c = BridgeConfig.from_env()
        assert len(c.agents) == 1
        assert c.agents[0].agent_id == "a1"
        assert isinstance(c.agents[0].target, HTTPTarget)

    def test_loads_multiple_agents(self) -> None:
        payload = json.dumps(
            [
                {
                    "agent_id": "weather",
                    "api_key": "k1",
                    "target": {"type": "http", "url": "https://w/inv"},
                },
                {
                    "agent_id": "math",
                    "api_key": "k2",
                    "target": {
                        "type": "agentcore",
                        "runtime_arn": "arn:x",
                        "region": "us-east-1",
                    },
                },
            ]
        )
        with patch.dict(os.environ, {"BAND_BRIDGE_AGENTS": payload}, clear=False):
            c = BridgeConfig.from_env()
        assert {a.agent_id for a in c.agents} == {"weather", "math"}
        assert isinstance(c.agents[0].target, HTTPTarget)
        assert isinstance(c.agents[1].target, AgentCoreTarget)

    def test_missing_env_var_raises(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "BAND_BRIDGE_AGENTS"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="BAND_BRIDGE_AGENTS"):
                BridgeConfig.from_env()

    def test_invalid_json_raises(self) -> None:
        with patch.dict(os.environ, {"BAND_BRIDGE_AGENTS": "not json"}, clear=False):
            with pytest.raises(ValueError, match="not valid JSON"):
                BridgeConfig.from_env()

    def test_non_list_json_raises(self) -> None:
        with patch.dict(
            os.environ, {"BAND_BRIDGE_AGENTS": '{"agent_id":"a"}'}, clear=False
        ):
            with pytest.raises(ValueError, match="JSON array"):
                BridgeConfig.from_env()

    def test_url_overrides(self) -> None:
        payload = json.dumps(
            [
                {
                    "agent_id": "a",
                    "api_key": "k",
                    "target": {"type": "http", "url": "https://x/y"},
                }
            ]
        )
        env = {
            "BAND_BRIDGE_AGENTS": payload,
            "BAND_WS_URL": "wss://staging/socket",
            "BAND_REST_URL": "https://staging.app",
            "HEALTH_PORT": "9000",
            "HEALTH_HOST": "127.0.0.1",
        }
        with patch.dict(os.environ, env, clear=False):
            c = BridgeConfig.from_env()
        assert c.ws_url == "wss://staging/socket"
        assert c.rest_url == "https://staging.app"
        assert c.health_port == 9000
        assert c.health_host == "127.0.0.1"

    def test_invalid_health_port_raises(self) -> None:
        payload = json.dumps(
            [
                {
                    "agent_id": "a",
                    "api_key": "k",
                    "target": {"type": "http", "url": "https://x/y"},
                }
            ]
        )
        with patch.dict(
            os.environ,
            {"BAND_BRIDGE_AGENTS": payload, "HEALTH_PORT": "abc"},
            clear=False,
        ):
            with pytest.raises(ValueError, match="HEALTH_PORT"):
                BridgeConfig.from_env()


class TestReconnectConfig:
    def test_defaults(self) -> None:
        c = ReconnectConfig()
        assert c.initial_delay == 1.0
        assert c.max_delay == 60.0
        assert c.multiplier == 2.0

    def test_rejects_non_positive_initial_delay(self) -> None:
        with pytest.raises(ValueError, match="initial_delay"):
            ReconnectConfig(initial_delay=0)

    def test_rejects_multiplier_below_one(self) -> None:
        with pytest.raises(ValueError, match="multiplier"):
            ReconnectConfig(multiplier=0.5)
