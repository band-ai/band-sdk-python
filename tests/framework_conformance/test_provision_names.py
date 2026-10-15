"""Each provision registers under its own agent name.

A leftover row for a label must not make the next provision of that label
ask the platform for the same name.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from band_rest import AgentRegisterRequest

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import (
    AGENT_NAME_MAX,
    MENTION_HANDLE_CAP,
    NAME_PREFIX,
    NAME_TOKEN_HEX,
    ResourceManager,
    name_budget,
    name_token,
    run_id_len,
)

REGISTERED_AGENT_ID = "registered-agent"


def recording_manager() -> tuple[ResourceManager, AsyncMock]:
    def respond(*, agent: AgentRegisterRequest) -> MagicMock:
        response = MagicMock()
        response.data.agent.id = REGISTERED_AGENT_ID
        response.data.credentials.api_key = "test-key"
        return response

    register = AsyncMock(side_effect=respond)
    client = MagicMock()
    client.human_api_agents.register_my_agent = register
    manager = ResourceManager(
        user_client=client,
        settings=cast(BaselineSettings, MagicMock()),
        run_id="session",
    )
    return manager, register


def registered_names(register: AsyncMock) -> list[str]:
    return [call.kwargs["agent"].name for call in register.await_args_list]


def name_parts(name: str) -> tuple[str, str]:
    """Hex token and label from a provisioned name. The token contains no hyphen."""
    token, _separator, label = name.removeprefix(NAME_PREFIX).partition("-")
    return token, label


def is_hex(token: str) -> bool:
    return bool(token) and all(character in "0123456789abcdef" for character in token)


class TestProvisionNames:
    async def test_same_mentioned_label_registers_two_names_inside_the_cap(
        self,
    ) -> None:
        manager, register = recording_manager()

        first = await manager.provision_agent("invitable")
        second = await manager.provision_agent("invitable")

        names = registered_names(register)
        assert names == [first.name, second.name]
        assert first.id == REGISTERED_AGENT_ID
        assert first.name != first.id
        assert len(set(names)) == 2
        for name in names:
            token, label = name_parts(name)
            assert name.startswith(NAME_PREFIX)
            assert label == "invitable"
            assert is_hex(token)
            assert len(token) == run_id_len()
            assert len(name) == MENTION_HANDLE_CAP

        short = await manager.provision_agent("echo")
        short_token, short_label = name_parts(short.name)
        assert short_label == "echo"
        assert len(short_token) == run_id_len()
        assert len(short.name) <= MENTION_HANDLE_CAP

    async def test_adapter_label_uses_a_wide_token_within_the_name_limit(
        self,
    ) -> None:
        manager, register = recording_manager()

        first = await manager.provision_agent("copilot_sdk")
        second = await manager.provision_agent("copilot_sdk")

        names = registered_names(register)
        assert names == [first.name, second.name]
        assert first.id == REGISTERED_AGENT_ID
        assert first.name != first.id
        assert len(set(names)) == 2
        for name in names:
            token, label = name_parts(name)
            assert label == "copilot-sdk"
            assert is_hex(token)
            assert len(token) == 32
            assert len(name) <= AGENT_NAME_MAX

    async def test_long_label_clamps_the_token_to_the_name_limit(self) -> None:
        manager, register = recording_manager()
        # One shorter than a full uuid, so the token has to shrink to fit.
        label = "x" * (name_budget(AGENT_NAME_MAX, 0) - (NAME_TOKEN_HEX - 1))

        await manager.provision_agent(label)

        token, parsed = name_parts(registered_names(register)[0])
        assert parsed == label
        assert len(token) < NAME_TOKEN_HEX
        assert len(token) >= run_id_len()
        assert len(registered_names(register)[0]) == AGENT_NAME_MAX

    def test_label_too_long_for_a_unique_name_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unique name token"):
            name_token("x" * 90)
