"""Read and bind native role definitions without a gateway-only route."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import JsonValue

from band.contracts.client import ContractClient
from band.contracts.errors import ContractIdentityError
from band.contracts.tools import BoundContractTools, ContractDefinitionInput
from tests.contracts.client.platform import PlatformRoomPeer


class TestNativeDefinition:
    async def test_definition_tool_reads_the_native_view_and_caches_an_isolated_copy(
        self,
    ) -> None:
        peer = PlatformRoomPeer()
        async with (
            peer.client() as http,
            ContractClient(peer.connection(), http=http) as client,
        ):
            room = await client.room(peer.room, peer.session, actor=peer.actor)
            tools = BoundContractTools(room)
            first = await tools.definition(ContractDefinitionInput())
            assert first == peer.definition
            contract = first["contract"]
            assert isinstance(contract, dict)
            contract["digest"] = "caller edit"
            second = await room.definition()

        assert second == peer.definition
        assert peer.routes == [
            ("GET", peer.path + "/v1/identity"),
            ("POST", peer.path + "/v1/view"),
        ]

    async def test_hydrated_native_view_preserves_the_authority_projection(
        self,
    ) -> None:
        peer = PlatformRoomPeer()
        async with (
            peer.client() as http,
            ContractClient(peer.connection(), http=http) as client,
        ):
            room = await client.room(peer.room, peer.session, actor=peer.actor)
            observation = await room.view(hydrate=True)

        assert observation == peer.observation
        assert peer.routes == [
            ("GET", peer.path + "/v1/identity"),
            ("POST", peer.path + "/v1/view"),
        ]

    @pytest.mark.parametrize(
        ("path", "value"),
        [
            pytest.param(("profile",), "another-view/1", id="view-profile"),
            pytest.param(("actor",), "Agent:other", id="actor"),
            pytest.param(("view", "id"), "another-session", id="view-session"),
            pytest.param(
                ("checkpoint", "state", "id"),
                "another-session",
                id="checkpoint-session",
            ),
            pytest.param(
                ("checkpoint", "definition"), "e" * 64, id="checkpoint-definition"
            ),
            pytest.param(("checkpoint", "source"), "b" * 64, id="checkpoint-source"),
            pytest.param(
                ("view", "definition", "contract", "digest"),
                "e" * 64,
                id="projected-definition",
            ),
            pytest.param(
                ("view", "definition", "contract", "source_digest"),
                "b" * 64,
                id="projected-source",
            ),
            pytest.param(
                ("view", "definition", "role", "name"), "observer", id="projected-role"
            ),
            pytest.param(
                ("checkpoint", "state", "bindings", "participant"),
                "Agent:other",
                id="role-binding",
            ),
            pytest.param(("view", "definition"), None, id="missing-definition"),
            pytest.param(
                ("view", "definition", "contract"), None, id="missing-contract"
            ),
            pytest.param(("view", "definition", "role"), None, id="missing-role"),
            pytest.param(("checkpoint",), None, id="missing-checkpoint"),
            pytest.param(("checkpoint", "state"), None, id="missing-state"),
            pytest.param(
                ("checkpoint", "state", "bindings"), None, id="missing-bindings"
            ),
        ],
    )
    async def test_unbound_native_definition_is_refused_and_never_cached(
        self, path: tuple[str, ...], value: JsonValue
    ) -> None:
        peer = PlatformRoomPeer()
        original = deepcopy(peer.observation)
        expected_definition = deepcopy(peer.definition)
        peer.replace(path, value)

        async with (
            peer.client() as http,
            ContractClient(peer.connection(), http=http) as client,
        ):
            room = await client.room(peer.room, peer.session, actor=peer.actor)
            with pytest.raises(ContractIdentityError):
                await room.definition()
            peer.observation = original
            definition = await room.definition()

        assert definition == expected_definition
        assert peer.routes == [
            ("GET", peer.path + "/v1/identity"),
            ("POST", peer.path + "/v1/view"),
            ("POST", peer.path + "/v1/view"),
        ]
