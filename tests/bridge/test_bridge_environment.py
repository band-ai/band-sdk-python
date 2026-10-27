"""BridgeConfig.from_env loads agents and overrides from the environment."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from bridge_core.config import (
    AgentCoreTarget,
    BridgeConfig,
    HTTPTarget,
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
        with (
            patch.dict(os.environ, env, clear=True),
            pytest.raises(ValueError, match="BAND_BRIDGE_AGENTS"),
        ):
            BridgeConfig.from_env()

    def test_invalid_json_raises(self) -> None:
        with (
            patch.dict(os.environ, {"BAND_BRIDGE_AGENTS": "not json"}, clear=False),
            pytest.raises(ValueError, match="not valid JSON"),
        ):
            BridgeConfig.from_env()

    def test_non_list_json_raises(self) -> None:
        with (
            patch.dict(
                os.environ, {"BAND_BRIDGE_AGENTS": '{"agent_id":"a"}'}, clear=False
            ),
            pytest.raises(ValueError, match="JSON array"),
        ):
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
        with (
            patch.dict(
                os.environ,
                {"BAND_BRIDGE_AGENTS": payload, "HEALTH_PORT": "abc"},
                clear=False,
            ),
            pytest.raises(ValueError, match="HEALTH_PORT"),
        ):
            BridgeConfig.from_env()

    def test_loads_authenticated_message_only_target(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "BAND_BRIDGE_AGENTS",
            json.dumps(
                [
                    {
                        "agent_id": "agent",
                        "api_key": "key",
                        "messages_only": True,
                        "target": {
                            "type": "http",
                            "url": "https://example.com/invocations",
                            "bearer_token": "configured-bearer",
                        },
                    }
                ]
            ),
        )
        agent = BridgeConfig.from_env().agents[0]
        assert agent.messages_only
        assert isinstance(agent.target, HTTPTarget)
        assert agent.target.bearer_token is not None
        assert agent.target.bearer_token.get_secret_value() == "configured-bearer"

    def test_validation_errors_do_not_expose_misspelled_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "private-misspelled-bearer"
        monkeypatch.setenv(
            "BAND_BRIDGE_AGENTS",
            json.dumps(
                [
                    {
                        "agent_id": "agent",
                        "api_key": "key",
                        "target": {
                            "type": "http",
                            "url": "https://example.com",
                            "bearer_tokn": secret,
                        },
                    }
                ]
            ),
        )
        with pytest.raises(ValueError) as error:
            BridgeConfig.from_env()
        assert "bearer_tokn" in str(error.value)
        assert secret not in str(error.value)
