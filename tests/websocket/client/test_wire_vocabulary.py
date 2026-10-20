"""Drift guards pinning the SDK's wire vocabulary to the backend and band-sdk-core."""

from __future__ import annotations

import band_sdk_core

from band.client.streaming import DeliveryStatus, WireEvent


class TestWireVocabulary:
    def test_delivery_status_enum_matches_backend_values(self):
        """Drift guard: these are the recipient delivery states the backend emits
        (thenvoi-platform chat_message.ex). The barrier relies on the exact strings."""
        assert {s.value for s in DeliveryStatus} == {
            "delivered",
            "processing",
            "processed",
            "failed",
        }

    def test_wire_event_matches_band_sdk_core_event_type(self):
        """Drift guard: WireEvent's members through AGENT_CONTROL are meant to
        mirror band_sdk_core.EventType's wire-name vocabulary exactly (they're
        kept as separate literals, not derived, since EventType is an opaque
        PyO3 type that can't be a StrEnum member's value)."""
        core_wire_names = {
            getattr(band_sdk_core.EventType, name).wire_name
            for name in dir(band_sdk_core.EventType)
            if isinstance(
                getattr(band_sdk_core.EventType, name), band_sdk_core.EventType
            )
        }
        assert core_wire_names <= {member.value for member in WireEvent}
