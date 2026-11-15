"""Held ephemeral ports stay occupied until release, then become bindable."""

from __future__ import annotations

import socket

import pytest

from tests.ports import HeldAdjacentPorts, HeldPort


def _bind(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, port))
    return sock


class TestHeldPort:
    def test_a_second_bind_fails_while_the_port_is_held(self) -> None:
        with HeldPort() as held, pytest.raises(OSError):
            _bind(held.host, held.port)

    def test_the_port_is_bindable_after_release(self) -> None:
        held = HeldPort()
        host, port = held.host, held.port
        held.release()
        stolen = _bind(host, port)
        stolen.close()

    def test_context_exit_releases_without_an_explicit_call(self) -> None:
        with HeldPort() as held:
            host, port = held.host, held.port
        stolen = _bind(host, port)
        stolen.close()

    def test_adjacent_ports_are_occupied_while_held(self) -> None:
        with HeldAdjacentPorts() as pair:
            with pytest.raises(OSError):
                _bind(pair.host, pair.port)
            with pytest.raises(OSError):
                _bind(pair.host, pair.port + 1)

    def test_adjacent_ports_are_bindable_after_release(self) -> None:
        with HeldAdjacentPorts() as pair:
            host, base, neighbor = pair.host, pair.port, pair.port + 1
        for port in (base, neighbor):
            stolen = _bind(host, port)
            stolen.close()
