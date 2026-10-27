"""Expose the participant helper through the integrations public API."""

from __future__ import annotations

from band import integrations
from band.integrations.base import check_and_format_participants


class TestIntegrationExports:
    def test_exports_the_implementation(self) -> None:
        assert (
            integrations.check_and_format_participants is check_and_format_participants
        )

    def test_declares_the_public_export(self) -> None:
        assert check_and_format_participants.__name__ in integrations.__all__
