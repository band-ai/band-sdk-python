"""BridgeConfig.from_env loads agents and overrides from the environment."""

from __future__ import annotations

import json
from typing import Any

import pytest
from bridge_core.config import (
    AgentCoreTarget,
    BridgeConfig,
    HTTPTarget,
)

AGENTS_VAR = "BAND_BRIDGE_AGENTS"


def http_agent(agent_id: str = "a", **target: Any) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "api_key": "k",
        "target": {"type": "http", "url": "https://x/y"} | target,
    }


class TestBridgeConfigFromEnv:
    def test_loads_http_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AGENTS_VAR, json.dumps([http_agent("a1")]))
        c = BridgeConfig.from_env()
        assert [a.agent_id for a in c.agents] == ["a1"]
        assert isinstance(c.agents[0].target, HTTPTarget)

    def test_loads_multiple_agents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agentcore = {
            "agent_id": "math",
            "api_key": "k2",
            "target": {
                "type": "agentcore",
                "runtime_arn": "arn:x",
                "region": "us-east-1",
            },
        }
        monkeypatch.setenv(AGENTS_VAR, json.dumps([http_agent("weather"), agentcore]))
        c = BridgeConfig.from_env()
        assert {a.agent_id for a in c.agents} == {"weather", "math"}
        assert isinstance(c.agents[0].target, HTTPTarget)
        assert isinstance(c.agents[1].target, AgentCoreTarget)

    def test_missing_env_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(AGENTS_VAR, raising=False)
        with pytest.raises(ValueError, match=AGENTS_VAR):
            BridgeConfig.from_env()

    def test_invalid_json_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AGENTS_VAR, "not json")
        with pytest.raises(ValueError, match="not valid JSON"):
            BridgeConfig.from_env()

    def test_non_list_json_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AGENTS_VAR, json.dumps(http_agent()))
        with pytest.raises(ValueError, match="JSON array"):
            BridgeConfig.from_env()

    def test_url_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AGENTS_VAR, json.dumps([http_agent()]))
        monkeypatch.setenv("BAND_WS_URL", "wss://staging/socket")
        monkeypatch.setenv("BAND_REST_URL", "https://staging.app")
        monkeypatch.setenv("HEALTH_PORT", "9000")
        monkeypatch.setenv("HEALTH_HOST", "127.0.0.1")
        c = BridgeConfig.from_env()
        assert c.ws_url == "wss://staging/socket"
        assert c.rest_url == "https://staging.app"
        assert c.health_port == 9000
        assert c.health_host == "127.0.0.1"

    def test_invalid_health_port_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AGENTS_VAR, json.dumps([http_agent()]))
        monkeypatch.setenv("HEALTH_PORT", "abc")
        with pytest.raises(ValueError, match="HEALTH_PORT"):
            BridgeConfig.from_env()

    def test_loads_authenticated_message_only_target(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = http_agent(bearer_token="configured-bearer") | {"messages_only": True}
        monkeypatch.setenv(AGENTS_VAR, json.dumps([agent]))
        loaded = BridgeConfig.from_env().agents[0]
        assert loaded.messages_only
        assert isinstance(loaded.target, HTTPTarget)
        assert loaded.target.bearer_token is not None
        assert loaded.target.bearer_token.get_secret_value() == "configured-bearer"

    def test_validation_errors_do_not_expose_misspelled_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "private-misspelled-bearer"
        monkeypatch.setenv(AGENTS_VAR, json.dumps([http_agent(bearer_tokn=secret)]))
        with pytest.raises(ValueError) as error:
            BridgeConfig.from_env()
        assert "bearer_tokn" in str(error.value)
        assert secret not in str(error.value)
