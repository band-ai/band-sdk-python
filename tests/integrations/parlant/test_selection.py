"""A model's decline decision must survive Parlant's tool selection."""

from __future__ import annotations

import pytest

from band.integrations.parlant.tools import create_parlant_tools
from band.runtime.tools.types import BandTool

pytest.importorskip("parlant.sdk")

from tests.integrations.parlant.inference import infer_calls


class TestParlantToolSelection:
    @pytest.mark.parametrize(
        ("applicable", "reason", "expected"),
        [
            (False, "The customer requested an access code and expects an answer", []),
            (True, "This is an FYI with no answer requested", [BandTool.NO_REPLY]),
        ],
    )
    async def test_no_reply_requires_an_affirmative_decision(
        self, applicable: bool, reason: str, expected: list[str]
    ) -> None:
        tool = next(
            entry.tool
            for entry in create_parlant_tools()
            if entry.tool.name == BandTool.NO_REPLY
        )
        assert await infer_calls(tool, applicable=applicable, reason=reason) == expected
