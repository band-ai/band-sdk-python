"""Durable phase records exclude verbose content and survive a failed scenario."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import band.adapters.opencode
from tests.e2e.baseline.toolkit.logs import sdk_logs_at


def test_phase_artifact_excludes_payloads_and_closes_after_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "artifacts" / "phases.log"
    logger = logging.getLogger(band.adapters.opencode.__name__)
    with (
        pytest.raises(RuntimeError, match="scenario failed"),
        sdk_logs_at(band.adapters.opencode, logging.DEBUG, path=path),
    ):
        logger.info("first provider event session=%s", "ses-test")
        logger.debug("tool arguments: secret-payload")
        logger.error("provider exception: secret-message")
        raise RuntimeError("scenario failed")

    contents = path.read_text(encoding="utf-8")
    assert "Z band.adapters.opencode first provider event session=ses-test" in contents
    assert "secret-" not in contents
    logger.info("outside scenario")
    assert path.read_text(encoding="utf-8") == contents
    path.unlink()
