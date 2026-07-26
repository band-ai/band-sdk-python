"""Tests for Parlant server port reservation."""

from __future__ import annotations

import contextlib
import socket

from band.integrations.parlant.ports import reserve_server_ports


def test_reserved_pair_is_two_distinct_ports() -> None:
    """Parlant binds both, so a pair that repeats a number cannot start a server."""
    ports = reserve_server_ports()

    assert ports.port != ports.tool_service_port


def test_reserved_ports_are_free_for_the_caller_to_bind() -> None:
    """The reservation is released — Parlant itself re-binds the numbers."""
    ports = reserve_server_ports()

    with contextlib.ExitStack() as stack:
        for port in ports:
            bound = stack.enter_context(
                socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            )
            bound.bind(("127.0.0.1", port))
