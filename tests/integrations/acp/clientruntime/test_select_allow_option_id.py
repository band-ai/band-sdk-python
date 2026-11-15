"""Which permission option an automatic allow selects."""

from __future__ import annotations

from band.integrations.acp.client_runtime import select_allow_option_id


class TestSelectAllowOptionId:
    def test_prefers_allow_once_over_allow_always(self) -> None:
        options = [
            {"kind": "allow_always", "optionId": "always"},
            {"kind": "allow_once", "optionId": "once"},
        ]
        assert select_allow_option_id(options) == "once"

    def test_no_allow_option_returns_none(self) -> None:
        assert (
            select_allow_option_id([{"kind": "reject_once", "optionId": "no"}]) is None
        )

    def test_present_but_empty_option_id_is_not_dropped(self) -> None:
        """An explicit (if empty) optionId must not fall through to the snake_case
        alias and get dropped — coalesce on absence, not falsiness."""
        assert select_allow_option_id([{"kind": "allow_once", "optionId": ""}]) == ""
