"""Bridge configuration: agent identities and forwarding targets.

The bridge is a dumb WS subscriber + event forwarder. Each agent identity
in :class:`BridgeConfig.agents` has its own Thenvoi WS subscription and its
own :class:`Target` describing where to forward events.

Loaded from a single ``THENVOI_BRIDGE_AGENTS`` JSON env var, e.g.::

    THENVOI_BRIDGE_AGENTS='[
      {"agent_id":"u1","api_key":"k1","target":{"type":"http","url":"https://w/inv"}},
      {"agent_id":"u2","api_key":"k2","target":{"type":"agentcore","runtime_arn":"arn:...","region":"us-east-1"}}
    ]'
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, field_validator


class HTTPTarget(BaseModel):
    """Forward events via HTTP POST to a plain URL."""

    type: Literal["http"] = "http"
    url: str
    timeout: float = 120.0

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("HTTPTarget.url must be non-empty")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("HTTPTarget.url must start with http:// or https://")
        return v

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("HTTPTarget.timeout must be positive")
        return v


class AgentCoreTarget(BaseModel):
    """Forward events via ``bedrock-agentcore:InvokeAgentRuntime``."""

    type: Literal["agentcore"] = "agentcore"
    runtime_arn: str
    region: str = "us-east-1"
    timeout: float = 120.0

    @field_validator("runtime_arn")
    @classmethod
    def validate_runtime_arn(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("AgentCoreTarget.runtime_arn must be non-empty")
        return v

    @field_validator("region")
    @classmethod
    def validate_region(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("AgentCoreTarget.region must be non-empty")
        return v

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("AgentCoreTarget.timeout must be positive")
        return v


Target = Annotated[
    Union[HTTPTarget, AgentCoreTarget],
    Field(discriminator="type"),
]


class AgentConfig(BaseModel):
    """One agent identity in the bridge.

    The bridge opens a Thenvoi WS subscription as ``agent_id`` (authed with
    ``api_key``) and forwards every event it receives to ``target``.
    """

    agent_id: str
    api_key: str = Field(repr=False)
    target: Target

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("AgentConfig.agent_id must be non-empty")
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("AgentConfig.api_key must be non-empty")
        return v


class BridgeConfig(BaseModel):
    """Bridge process configuration."""

    agents: list[AgentConfig]
    ws_url: str = "wss://app.thenvoi.com/api/v1/socket/websocket"
    rest_url: str = "https://app.thenvoi.com"
    health_port: int = 8080
    health_host: str = "0.0.0.0"
    # Per-agent cap on concurrent in-flight forwards. One asyncio task is
    # spawned per WS event; without a cap, a burst of events (or a slow target)
    # can pile up thousands of tasks waiting on the per-room lock or the
    # target's I/O, and fan a flood of HTTP/AgentCore calls at the backend.
    max_concurrent_forwards: int = 32

    @field_validator("max_concurrent_forwards")
    @classmethod
    def validate_max_concurrent_forwards(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_concurrent_forwards must be >= 1, got: {v}")
        return v

    @field_validator("agents")
    @classmethod
    def validate_agents(cls, v: list[AgentConfig]) -> list[AgentConfig]:
        if not v:
            raise ValueError("BridgeConfig.agents must contain at least one entry")
        agent_ids = [a.agent_id for a in v]
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("Duplicate agent_id in BridgeConfig.agents")
        return v

    @field_validator("health_port")
    @classmethod
    def validate_health_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"health_port must be between 1 and 65535, got: {v}")
        return v

    @classmethod
    def from_env(cls) -> BridgeConfig:
        """Load configuration from ``THENVOI_BRIDGE_AGENTS`` (JSON) plus optional overrides.

        Raises:
            ValueError: If ``THENVOI_BRIDGE_AGENTS`` is missing or invalid.
        """
        agents_json = os.environ.get("THENVOI_BRIDGE_AGENTS")
        if not agents_json or not agents_json.strip():
            raise ValueError(
                "THENVOI_BRIDGE_AGENTS environment variable is required "
                "(JSON list of agent configs)"
            )
        try:
            agents_data = json.loads(agents_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"THENVOI_BRIDGE_AGENTS is not valid JSON: {e}") from e
        if not isinstance(agents_data, list):
            raise ValueError("THENVOI_BRIDGE_AGENTS must be a JSON array")

        kwargs: dict[str, Any] = {"agents": agents_data}

        if "THENVOI_WS_URL" in os.environ:
            kwargs["ws_url"] = os.environ["THENVOI_WS_URL"]
        if "THENVOI_REST_URL" in os.environ:
            kwargs["rest_url"] = os.environ["THENVOI_REST_URL"]
        if "HEALTH_HOST" in os.environ:
            kwargs["health_host"] = os.environ["HEALTH_HOST"]
        if "HEALTH_PORT" in os.environ:
            raw = os.environ["HEALTH_PORT"]
            try:
                kwargs["health_port"] = int(raw)
            except ValueError:
                raise ValueError(
                    f"HEALTH_PORT must be a valid integer, got: '{raw}'"
                ) from None

        return cls(**kwargs)


class ReconnectConfig(BaseModel):
    """Per-agent reconnection backoff."""

    initial_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0
    jitter: float = 0.5
    max_retries: int = 0  # 0 = unlimited

    @field_validator("initial_delay")
    @classmethod
    def validate_initial_delay(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"initial_delay must be positive, got: {v}")
        return v

    @field_validator("max_delay")
    @classmethod
    def validate_max_delay(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"max_delay must be positive, got: {v}")
        return v

    @field_validator("multiplier")
    @classmethod
    def validate_multiplier(cls, v: float) -> float:
        if v < 1:
            raise ValueError(f"multiplier must be >= 1, got: {v}")
        return v

    @field_validator("jitter")
    @classmethod
    def validate_jitter(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"jitter must be non-negative, got: {v}")
        return v

    @field_validator("max_retries")
    @classmethod
    def validate_max_retries(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"max_retries must be non-negative, got: {v}")
        return v
