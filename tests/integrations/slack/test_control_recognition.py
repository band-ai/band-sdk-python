"""A Slack-wrapped adapter keeps its inner adapter's control recognition."""

from __future__ import annotations

import pytest

from tests.adapters.codex.kit import make_platform_message
from tests.integrations.slack.wrappingkit import CommandBrain, _make_adapter


class TestSlackControlRecognition:
    @pytest.mark.parametrize(
        ("content", "recognized"),
        [("/approve abc", True), ("an ordinary request", False)],
    )
    def test_wrapper_recognizes_what_its_inner_adapter_does(
        self, content: str, recognized: bool
    ) -> None:
        wrapper, _, _, _ = _make_adapter(inner=CommandBrain())

        assert (
            wrapper.is_control_message(make_platform_message(content=content))
            is recognized
        )
