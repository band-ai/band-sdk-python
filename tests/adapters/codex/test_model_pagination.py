"""Room startup, model listing and effort lookup share complete pagination."""

from __future__ import annotations

import pytest

from band.core.defaultmodels import OPENAI_MODEL
from tests.adapters.codex.kit import ROOM_ID, ToolSchemaFakeTools, send_bootstrap
from tests.adapters.codex.probes import ProbeHost


class TestModelPagination:
    async def test_startup_and_room_listing_find_visible_later_page_models(
        self, probe_host: ProbeHost
    ) -> None:
        adapter = probe_host.adapter(
            pages={
                "": {
                    "data": [{"id": "hidden-first", "hidden": True}],
                    "nextCursor": "next",
                },
                "next": {"data": [{"id": "visible-second"}], "nextCursor": None},
            }
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        await send_bootstrap(adapter, content="/model list", tools=tools)

        assert adapter._room_clients[ROOM_ID].selected_model == "visible-second"
        assert "visible-second" in tools.messages_sent[0]["content"]
        assert "hidden-first" not in tools.messages_sent[0]["content"]
        assert "thread/start" not in probe_host.methods
        assert "turn/start" not in probe_host.methods
        await adapter.cleanup_all()
        probe_host.assert_exited()

    async def test_configured_model_efforts_on_later_page_validate_room_command(
        self, probe_host: ProbeHost
    ) -> None:
        adapter = probe_host.adapter(
            model="configured-model",
            pages={
                "": {"data": [{"id": "other-model"}], "nextCursor": "next"},
                "next": {
                    "data": [
                        {
                            "id": "configured-model",
                            "supportedReasoningEfforts": [
                                {"reasoningEffort": "low"},
                                {"reasoningEffort": "ultra"},
                            ],
                        }
                    ],
                    "nextCursor": None,
                },
            },
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        await send_bootstrap(adapter, content="/reasoning typo", tools=tools)

        assert adapter._room_clients[ROOM_ID].reasoning_effort is None
        assert adapter.config.model == "configured-model"
        assert "turn/start" not in probe_host.methods
        await adapter.cleanup_all()
        probe_host.assert_exited()

    @pytest.mark.parametrize("endless", [False, True], ids=["repeated", "page-ceiling"])
    async def test_bad_pagination_retains_the_existing_startup_default_policy(
        self, probe_host: ProbeHost, endless: bool
    ) -> None:
        adapter = probe_host.adapter(
            pages={
                "": {"data": [{"id": "incomplete-catalog"}], "nextCursor": "same"},
                "same": {"data": [], "nextCursor": "same"},
            },
            endless_pages=endless,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        await send_bootstrap(adapter, content="/model", tools=tools)

        assert adapter._room_clients[ROOM_ID].selected_model == OPENAI_MODEL
        await adapter.cleanup_all()
        probe_host.assert_exited()
