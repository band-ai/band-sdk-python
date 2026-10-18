"""Native Codex catalogs do not establish the configured provider's identity."""

from __future__ import annotations

from tests.adapters.codex.probes import ProbeHost


class TestModelProvider:
    async def test_unreported_provider_is_unknown_even_for_a_configured_model(
        self, probe_host: ProbeHost
    ) -> None:
        adapter = probe_host.adapter(
            model="private-provider-model",
            pages={
                "": {
                    "data": [
                        {
                            "id": "private-provider-model",
                            "model": "private-provider-model",
                            "displayName": "Private model",
                            "hidden": False,
                            "isDefault": True,
                            "supportedReasoningEfforts": [],
                        }
                    ],
                    "nextCursor": None,
                }
            },
        )

        catalog = await adapter.probe_models(timeout_s=5)

        assert catalog.model_ids == ("private-provider-model",)
        assert catalog.models[0].provider is None
        assert adapter.config.model == "private-provider-model"
        probe_host.assert_exited()
