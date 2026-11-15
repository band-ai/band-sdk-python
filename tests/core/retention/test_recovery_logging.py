"""Recovery diagnostics exclude room identifiers and raw failure-report exceptions."""

from __future__ import annotations

import logging

import pytest

from band.core.exceptions import ProviderUnavailableError
from band.core.turn_lifecycle import BackendTurns
from tests.core.recordingcancel import RecordingCancel
from tests.core.retention.kit import ResidenceHost
from tests.core.retention.reporting import (
    SENSITIVE_RECOVERY_DETAIL,
    ThrowingFailureTools,
)


class TestRecoveryLogging:
    async def test_recovery_warnings_include_only_failure_classes(
        self, host: ResidenceHost, caplog: pytest.LogCaptureFixture
    ) -> None:
        resident = host.room(SENSITIVE_RECOVERY_DETAIL)
        await resident.turn()
        resident.fail_close = True
        turns = BackendTurns(RecordingCancel(), provider="test")
        turns.recovering(
            SENSITIVE_RECOVERY_DETAIL,
            "operation",
            resident.room,
            generation=resident.room.generation,
        )
        with (
            caplog.at_level(logging.WARNING, logger="band.core.turn_lifecycle"),
            pytest.raises(ProviderUnavailableError),
        ):
            await turns.require_admission(
                SENSITIVE_RECOVERY_DETAIL, ThrowingFailureTools()
            )
        records = [
            record
            for record in caplog.records
            if record.name == "band.core.turn_lifecycle"
        ]
        assert [
            (record.levelno, record.getMessage(), record.exc_info) for record in records
        ] == [
            (
                logging.WARNING,
                "Backend recovery failed (RuntimeError); marking room unavailable",
                None,
            ),
            (
                logging.WARNING,
                "Could not report unavailable backend (RuntimeError)",
                None,
            ),
        ]
