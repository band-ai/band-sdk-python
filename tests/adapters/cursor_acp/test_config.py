"""Cursor ACP adapter config validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.cursor_acp import (
    CursorACPAdapter,
    CursorACPAdapterConfig,
)


class TestCursorACPAdapterConfig:
    @pytest.mark.parametrize(
        ("settings", "error"),
        [
            ({"api_key": "a", "auth_token": "b"}, "either api_key or auth_token"),
            ({"command": ()}, "requires a command"),
            (
                {"auth_method": "api_key"},
                "auth_method\n  Input should be 'cursor_login'",
            ),
            (
                {"decision_timeout_s": 0.0},
                "decision_timeout_s\n  Input should be greater than 0",
            ),
            (
                {"max_pending_decisions": 0},
                "max_pending_decisions\n  Input should be greater than 0",
            ),
            (
                {"decision_timeout_s": 300.0, "turn_timeout_s": 300.0},
                "decision_timeout_s must be less than turn_timeout_s",
            ),
            (
                {"decision_timeout_s": 900.0},
                "decision_timeout_s must be less than turn_timeout_s",
            ),
        ],
        ids=[
            "ambiguous-auth",
            "empty-command",
            "foreign-auth-method",
            "non-positive-decision-timeout",
            "non-positive-max-pending",
            "decision-timeout-equals-turn-timeout",
            "decision-timeout-reaching-the-default-turn-timeout",
        ],
    )
    def test_rejects_invalid_settings(
        self, settings: dict[str, object], error: str
    ) -> None:
        with pytest.raises(ValueError, match=error):
            CursorACPAdapterConfig.model_validate(settings)

    def test_authorized_senders_load_from_any_list_of_ids(self) -> None:
        config = CursorACPAdapterConfig.model_validate(
            {"decision_authorized_senders": ["owner", "owner", "admin"]}
        )

        assert config.decision_authorized_senders == frozenset({"owner", "admin"})

    def test_cwd_and_a_workspace_resolver_are_exclusive(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="either cwd or workspace_for_room"):
            CursorACPAdapter(
                CursorACPAdapterConfig(cwd=str(tmp_path)),
                workspace_for_room=lambda room_id: str(tmp_path / room_id),
            )
