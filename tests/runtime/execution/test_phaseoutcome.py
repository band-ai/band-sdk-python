from __future__ import annotations

import logging

import pytest

from band.runtime.delivery import observe_delivery_phase


class TestPhaseOutcome:
    """Boundary traces stay at DEBUG and name interrupts that are not Exceptions."""

    def test_completed_phase_is_debug_without_a_monotonic_stamp(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="band.runtime.delivery")
        with observe_delivery_phase("handler", "room-1", "msg-1"):
            pass
        assert "phase=handler" not in caplog.text

        caplog.clear()
        caplog.set_level(logging.DEBUG, logger="band.runtime.delivery")
        with observe_delivery_phase("handler", "room-1", "msg-1"):
            pass
        assert "phase=handler state=started room=room-1 message=msg-1" in caplog.text
        assert "phase=handler state=returned room=room-1 message=msg-1" in caplog.text

    @pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
    def test_interrupt_is_named(
        self, caplog: pytest.LogCaptureFixture, interrupt: type[BaseException]
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="band.runtime.delivery")
        with (
            pytest.raises(interrupt),
            observe_delivery_phase("handler", "room-1", "msg-1"),
        ):
            raise interrupt
        assert f"phase=handler state={interrupt.__name__}" in caplog.text
        assert "state=returned" not in caplog.text
