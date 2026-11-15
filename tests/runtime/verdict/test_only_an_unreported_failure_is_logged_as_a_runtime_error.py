from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from tests.runtime.helpers import (
    deliver,
    run_through,
)
from tests.runtime.verdict.kit import (
    ScriptedAdapter,
    Step,
    crash,
    delivery_logger,
)


class TestOnlyAnUnreportedFailureIsLoggedAsARuntimeError:
    @pytest.mark.parametrize("path", ["live", "backlog"])
    @pytest.mark.parametrize(
        ("steps", "level", "traceback"),
        [
            pytest.param([], logging.DEBUG, False, id="missing-reply"),
            pytest.param([crash], logging.ERROR, True, id="crash"),
        ],
    )
    async def test_only_an_unreported_failure_is_logged_as_a_runtime_error(
        self,
        link: MagicMock,
        caplog: pytest.LogCaptureFixture,
        path: str,
        steps: list[Step],
        level: int,
        traceback: bool,
    ) -> None:
        """A missing reply was logged as a WARNING where it was reported, so the
        runtime keeps it out of ERROR alerting; a crash still alerts with its
        traceback."""
        ctx = run_through(ScriptedAdapter(steps), link)

        with caplog.at_level(logging.DEBUG, logger=delivery_logger):
            await deliver(ctx, path)

        (record,) = [
            r for r in caplog.records if r.message.startswith("Error processing")
        ]
        assert record.levelno == level
        assert bool(record.exc_info) is traceback
