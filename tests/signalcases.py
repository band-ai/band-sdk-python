"""Shared parametrize rows for install_signal_handlers forwarding tests."""

from __future__ import annotations

INSTALL_SIGNAL_HANDLER_CASES: list[tuple[dict[str, bool], bool]] = [
    ({}, True),
    ({"install_signal_handlers": False}, False),
]
INSTALL_SIGNAL_HANDLER_IDS: list[str] = ["script-default", "host-owns-signals"]
