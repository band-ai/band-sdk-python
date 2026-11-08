"""OpenAPI calls intersect documented paths with host-approved servers and IDs."""

from __future__ import annotations

from pathlib import Path

import pytest
from bandpap.channels import OpenAPIClient
from bandpap.discovery import ApiDiscovery

from tests.pap.channelkit import connection, schema


class TestOpenAPIChannel:
    async def test_documented_get_and_json_post_use_resource_bound_dpop(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        client = OpenAPIClient(
            oauth,
            oauth.discovery.document.apis[0],
            allowed_servers=frozenset({"https://api.company.example/v1"}),
            allowed_operations=frozenset({"getOrder", "createOrder"}),
        )
        transport.responses.extend([schema(), {"order": "one"}, {"created": True}])
        loaded = await client.load()
        loaded["paths"].clear()
        await client.call(
            "getOrder",
            server="https://api.company.example/v1",
            path_parameters={"order": "one two"},
            query={"fields": ["id", "status"]},
        )
        assert (
            transport.requests[-1]["url"]
            == "https://api.company.example/v1/orders/one%20two?fields=id&fields=status"
        )
        await client.call(
            "createOrder",
            server="https://api.company.example/v1",
            document={"custom": "terms"},
        )
        request = transport.requests[-1]
        assert request["method"] == "POST"
        assert request["document"] == {"custom": "terms"}
        assert (
            request["headers"]["Authorization"]
            == "DPoP private-token-DPoP:https://api.company.example"
        )
        assert "DPoP" in request["headers"]
        store.close()

    async def test_unadvertised_api_and_unapproved_servers_cannot_receive_tokens(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        with pytest.raises(ValueError, match="advertised"):
            OpenAPIClient(
                oauth,
                ApiDiscovery(
                    type="openapi",
                    url="https://attacker.example/schema",
                    description="fake",
                ),
                allowed_servers=frozenset(),
                allowed_operations=frozenset(),
            )
        client = OpenAPIClient(
            oauth,
            oauth.discovery.document.apis[0],
            allowed_servers=frozenset({"https://attacker.example"}),
            allowed_operations=frozenset({"getOrder"}),
        )
        transport.responses.append(schema())
        await client.load()
        for server in ("https://attacker.example", "https://api.company.example/v1"):
            with pytest.raises(PermissionError):
                await client.call(
                    "getOrder", server=server, path_parameters={"order": "one"}
                )
        assert len(transport.requests) == 1
        store.close()

    async def test_undocumented_parameters_and_path_traversal_fail_before_request(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        client = OpenAPIClient(
            oauth,
            oauth.discovery.document.apis[0],
            allowed_servers=frozenset({"https://api.company.example/v1"}),
            allowed_operations=frozenset({"getOrder", "missing"}),
        )
        transport.responses.append(schema())
        await client.load()
        for value in ("..", "../secret", "%2e%2e", "a\\b"):
            with pytest.raises(ValueError):
                await client.call(
                    "getOrder",
                    server="https://api.company.example/v1",
                    path_parameters={"order": value},
                )
        with pytest.raises(ValueError):
            await client.call(
                "getOrder",
                server="https://api.company.example/v1",
                path_parameters={"order": "one"},
                query={"secret": "x"},
            )
        with pytest.raises(ValueError):
            await client.call("missing", server="https://api.company.example/v1")
        with pytest.raises(PermissionError):
            await client.call("unapproved", server="https://api.company.example/v1")
        assert len(transport.requests) == 1
        store.close()

    async def test_external_parameter_reference_is_explicitly_unsupported(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        client = OpenAPIClient(
            oauth,
            oauth.discovery.document.apis[0],
            allowed_servers=frozenset({"https://api.company.example/v1"}),
            allowed_operations=frozenset({"getOrder"}),
        )
        value = schema()
        value["paths"]["/orders/{order}"]["parameters"] = [
            {"$ref": "https://attacker.example/private"}
        ]
        transport.responses.append(value)
        await client.load()
        with pytest.raises(ValueError, match="Unsupported API parameter"):
            await client.call(
                "getOrder",
                server="https://api.company.example/v1",
                path_parameters={"order": "one"},
            )
        assert len(transport.requests) == 1
        store.close()

    @pytest.mark.parametrize("level", ["document", "path", "operation"])
    @pytest.mark.parametrize("servers", [None, {}, "https://api.company.example/v1"])
    async def test_malformed_servers_fail_before_authenticated_dispatch(
        self, tmp_path: Path, level: str, servers: object
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        client = OpenAPIClient(
            oauth,
            oauth.discovery.document.apis[0],
            allowed_servers=frozenset({"https://api.company.example/v1"}),
            allowed_operations=frozenset({"getOrder"}),
        )
        value = schema()
        target = value
        if level != "document":
            target = value["paths"]["/orders/{order}"]
        if level == "operation":
            target = target["get"]
        target["servers"] = servers
        transport.responses.append(value)
        try:
            await client.load()
            with pytest.raises(ValueError, match="servers must be an array"):
                await client.call(
                    "getOrder",
                    server="https://api.company.example/v1",
                    path_parameters={"order": "one"},
                )
            assert len(transport.requests) == 1
        finally:
            store.close()
