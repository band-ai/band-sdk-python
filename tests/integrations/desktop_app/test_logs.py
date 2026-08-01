"""Desktop room-view logging routes diagnostics to stderr and a rotating file."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from band import CHATTY_LOGGERS
from band.integrations.desktop_app import logs as desktop_logs
from tests.logsupport import (
    band_log_env,
    restored_logging,
)


def test_configure_writes_stderr_and_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_dir = tmp_path / "state"
    log_file = state_dir / "band-room-view.log"
    monkeypatch.setattr(desktop_logs, "STATE_DIR", state_dir)
    monkeypatch.setattr(desktop_logs, "LOG_FILE", log_file)

    with (
        restored_logging(
            "desktop.worker",
            *CHATTY_LOGGERS,
            "mcp.server.lowlevel.server",
        ),
        band_log_env(monkeypatch, FILE=None),
    ):
        desktop_logs.configure()
        logging.getLogger("band.integrations.desktop_app").info("room tick")
        logging.getLogger("desktop.worker").info("worker tick")
        logging.getLogger("httpx").info("http chatter")
        captured = capsys.readouterr()

    file_text = log_file.read_text()
    assert "room tick" in captured.err
    assert "worker tick" in captured.err
    assert "room tick" not in captured.out
    assert "http chatter" not in captured.err
    assert "room tick" in file_text
    assert "worker tick" in file_text
