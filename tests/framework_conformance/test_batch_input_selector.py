"""The baseline's ``batch_input`` selector follows each adapter class's own declaration."""

from __future__ import annotations

import pytest

from band.adapters.google_adk import GoogleADKAdapter
from tests.e2e.baseline.toolkit.adapters import Adapter, registered_ids, specs


class TestBatchInputSelector:
    @pytest.mark.parametrize("declares_support", [False, True])
    @pytest.mark.parametrize("batch_input", [False, True])
    def test_it_selects_by_the_adapter_class_declaration(
        self, monkeypatch: pytest.MonkeyPatch, declares_support: bool, batch_input: bool
    ) -> None:
        monkeypatch.setattr(GoogleADKAdapter, "supports_batch_input", declares_support)
        selected = {
            spec.id
            for spec in specs(include={Adapter.GOOGLE_ADK}, batch_input=batch_input)
        }

        expected = {Adapter.GOOGLE_ADK} if declares_support == batch_input else set()
        assert selected == expected

    def test_the_two_sides_of_the_selector_partition_the_registry(self) -> None:
        batching = {spec.id for spec in specs(batch_input=True, include_pending=True)}
        singleton = {spec.id for spec in specs(batch_input=False, include_pending=True)}

        assert batching.isdisjoint(singleton)
        assert batching | singleton == registered_ids()
