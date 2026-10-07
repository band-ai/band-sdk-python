"""Scoped SDK log levels: raised for a scenario, never lowered, always restored."""

from __future__ import annotations

import logging

import pytest

import band.adapters.opencode
from tests.e2e.baseline.toolkit.logs import sdk_logs_at
from tests.logsupport import restored_logging

PACKAGE = band.adapters.opencode


@pytest.mark.parametrize(
    ("configured", "inside"),
    [
        pytest.param(logging.NOTSET, logging.INFO, id="inherited-warning-is-raised"),
        pytest.param(logging.DEBUG, logging.DEBUG, id="debug-run-keeps-debug"),
    ],
)
def test_scope_shows_at_least_the_requested_level(configured: int, inside: int) -> None:
    with restored_logging(PACKAGE.__name__):
        logging.getLogger().setLevel(logging.WARNING)
        logger = logging.getLogger(PACKAGE.__name__)
        logger.setLevel(configured)

        with sdk_logs_at(PACKAGE, logging.INFO):
            assert logger.getEffectiveLevel() == inside
        assert logger.level == configured


def test_scope_restores_the_level_when_the_scenario_fails() -> None:
    with restored_logging(PACKAGE.__name__):
        logger = logging.getLogger(PACKAGE.__name__)
        logger.setLevel(logging.NOTSET)

        with pytest.raises(RuntimeError), sdk_logs_at(PACKAGE, logging.INFO):
            raise RuntimeError("scenario failed")
        assert logger.level == logging.NOTSET
