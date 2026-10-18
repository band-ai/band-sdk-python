"""Model probes return the visible paginated catalog without provider turns."""

from __future__ import annotations

import asyncio

import pytest

from band.core.model_catalog import ModelCatalog
from band.integrations.codex.rpc_base import CodexJsonRpcError
from tests.adapters.codex.kit import ROOM_ID, send_bootstrap
from tests.adapters.codex.probes import ProbeHost


class TestModelProbe:
    async def test_visible_paginated_metadata_uses_the_canonical_catalog(
        self, probe_host: ProbeHost
    ) -> None:
        adapter = probe_host.adapter(
            model="configured-room-model",
            pages={
                "": {
                    "data": [{"id": "internal-preview", "hidden": True}],
                    "nextCursor": "page-two",
                },
                "page-two": {
                    "data": [
                        {
                            "id": "gpt-6-sol",
                            "model": "gpt-6-sol",
                            "displayName": "GPT-6 Sol",
                            "hidden": False,
                            "isDefault": True,
                            "defaultReasoningEffort": "medium",
                            "supportedReasoningEfforts": [
                                {
                                    "reasoningEffort": "medium",
                                    "description": "Balanced",
                                },
                                {"reasoningEffort": "max", "description": "Thorough"},
                            ],
                        },
                        {"id": "no-effort", "supportedReasoningEfforts": []},
                        {"id": "unknown-efforts"},
                        {"displayName": "Not a selectable model"},
                    ],
                    "nextCursor": None,
                },
            },
        )

        catalog = await adapter.probe_models(timeout_s=5)

        assert isinstance(catalog, ModelCatalog)
        assert catalog.model_ids == ("gpt-6-sol", "no-effort", "unknown-efforts")
        assert catalog.current_model is None
        model = catalog.choice("gpt-6-sol")
        assert model is not None
        assert model.label == "GPT-6 Sol"
        assert model.provider is None
        assert model.is_default
        assert model.efforts == ("medium", "max")
        assert model.default_effort == "medium"
        assert catalog.choice("no-effort").efforts == ()
        assert catalog.choice("unknown-efforts").efforts is None
        assert [
            message["params"]
            for message in probe_host.messages
            if message["method"] == "model/list"
        ] == [{}, {"cursor": "page-two"}]
        assert probe_host.methods == [
            "initialize",
            "initialized",
            "model/list",
            "model/list",
        ]
        assert adapter._room_clients == {}
        assert adapter._workspaces.rooms == ()
        assert adapter.config.model == "configured-room-model"
        probe_host.assert_exited()

    async def test_empty_and_unsupported_are_not_conflated(
        self, probe_host: ProbeHost
    ) -> None:
        empty = probe_host.adapter()
        unsupported = probe_host.adapter(
            pages={"": {"error": {"code": -32601, "message": "model/list unsupported"}}}
        )

        assert (await empty.probe_models(timeout_s=5)).models == ()
        with pytest.raises(CodexJsonRpcError, match="unsupported"):
            await unsupported.probe_models(timeout_s=5)
        probe_host.assert_exited()

    async def test_cancellation_during_model_listing_reaps_the_probe(
        self, probe_host: ProbeHost
    ) -> None:
        adapter = probe_host.adapter(pages={"": {"hang": True}})
        probing = asyncio.create_task(adapter.probe_models(timeout_s=5))
        try:
            await probe_host.received("model/list")
            probing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await probing
            await adapter.cleanup_all()
            probe_host.assert_exited()
        finally:
            probing.cancel()
            await asyncio.gather(probing, return_exceptions=True)

    async def test_second_probe_does_not_reuse_a_closed_transport(
        self, probe_host: ProbeHost
    ) -> None:
        adapter = probe_host.adapter()
        assert (await adapter.probe_models(timeout_s=5)).models == ()
        assert (await adapter.probe_models(timeout_s=5)).models == ()
        assert len(probe_host.clients) == 2
        probe_host.assert_exited()

    async def test_probe_does_not_change_or_close_an_existing_rooms_selection(
        self, probe_host: ProbeHost
    ) -> None:
        adapter = probe_host.adapter(
            model="configured-model",
            pages={"": {"data": [{"id": "catalog-default", "isDefault": True}]}},
        )
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="/model room-override")
        [room_client] = probe_host.clients

        catalog = await adapter.probe_models(timeout_s=5)

        assert catalog.model_ids == ("catalog-default",)
        assert catalog.current_model is None
        assert adapter.config.model == "configured-model"
        assert adapter._room_clients[ROOM_ID].selected_model == "room-override"
        assert adapter._workspaces.rooms == (ROOM_ID,)
        assert room_client._proc is not None
        assert room_client._proc.returncode is None
        probe_client = probe_host.clients[-1]
        assert probe_client is not room_client
        assert probe_client._proc is not None
        assert probe_client._proc.returncode is not None
        assert "thread/start" not in probe_host.methods
        assert "turn/start" not in probe_host.methods
        await adapter.cleanup_all()
        probe_host.assert_exited()

    @pytest.mark.parametrize("method", ["initialize", "model/list"])
    async def test_probe_rpc_timeout_propagates_after_reaping(
        self, probe_host: ProbeHost, method: str
    ) -> None:
        adapter = probe_host.adapter(
            replies={method: {"hang": True}}, pages={"": {"hang": True}}
        )
        with pytest.raises(TimeoutError):
            await adapter.probe_models(timeout_s=1)
        probe_host.assert_exited()

    @pytest.mark.parametrize("endless", [False, True], ids=["repeated", "page-ceiling"])
    async def test_broken_pagination_is_bounded_and_closes_the_probe(
        self, probe_host: ProbeHost, endless: bool
    ) -> None:
        adapter = probe_host.adapter(
            pages={
                "": {"data": [], "nextCursor": "same"},
                "same": {"data": [], "nextCursor": "same"},
            },
            endless_pages=endless,
        )
        expected_error = "more than 50 pages" if endless else "repeated cursor"
        with pytest.raises(RuntimeError, match=expected_error):
            await adapter.probe_models(timeout_s=5)
        assert probe_host.methods.count("model/list") == (50 if endless else 2)
        probe_host.assert_exited()
