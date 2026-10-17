"""Resolve, validate and dial the same numeric address; retain TLS host verification."""

from __future__ import annotations

import asyncio
import socket
import ssl
from unittest.mock import AsyncMock, MagicMock

import pytest
from bandpap.fetch import DiscoveryFailure, PublicNetwork, PublicStream


class TestNetwork:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "192.168.1.1",
            "100.64.0.1",
            "224.0.0.1",
            "0.0.0.0",
            "::1",
            "fd00::1",
            "fe80::1",
            "::ffff:127.0.0.1",
            "64:ff9b::7f00:1",
            "2002:7f00:1::",
        ],
    )
    async def test_nonpublic_addresses_are_rejected_before_dial(
        self, monkeypatch: pytest.MonkeyPatch, address: str
    ) -> None:
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(
                return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
                ]
            ),
        )
        dial = AsyncMock()
        monkeypatch.setattr(asyncio, "open_connection", dial)
        with pytest.raises(DiscoveryFailure, match="public"):
            await PublicNetwork().connect_tcp("attacker.example", 443)
        dial.assert_not_awaited()

    async def test_mixed_public_private_dns_answer_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        addresses = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
            for address in ["8.8.8.8", "127.0.0.1"]
        ]
        monkeypatch.setattr(
            asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=addresses)
        )
        with pytest.raises(DiscoveryFailure, match="public"):
            await PublicNetwork().connect_tcp("attacker.example", 443)

    async def test_validated_ip_is_dialed_without_second_dns_and_tls_uses_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolve = AsyncMock(
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        )
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        writer = MagicMock(start_tls=AsyncMock(), wait_closed=AsyncMock())
        dial = AsyncMock(return_value=(asyncio.StreamReader(), writer))
        monkeypatch.setattr(asyncio, "open_connection", dial)
        stream = await PublicNetwork().connect_tcp("original.example", 443)
        dial.assert_awaited_once_with(
            "8.8.8.8", 443, family=socket.AF_INET, flags=socket.AI_NUMERICHOST
        )
        context = ssl.create_default_context()
        await stream.start_tls(context, "original.example")
        writer.start_tls.assert_awaited_once_with(
            context, server_hostname="original.example"
        )
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert resolve.await_count == 1
        await stream.aclose()
        writer.transport.abort.assert_called_once()
        writer.wait_closed.assert_not_awaited()

    async def test_tls_failure_propagates(self) -> None:
        writer = MagicMock(
            start_tls=AsyncMock(
                side_effect=ssl.SSLCertVerificationError("bad certificate")
            )
        )
        stream = PublicStream(asyncio.StreamReader(), writer)
        with pytest.raises(ssl.SSLCertVerificationError):
            await stream.start_tls(ssl.create_default_context(), "original.example")

    async def test_stalled_tls_shutdown_is_aborted_without_waiting(self) -> None:
        writer = MagicMock(
            wait_closed=AsyncMock(side_effect=AssertionError("must not wait for peer"))
        )
        stream = PublicStream(asyncio.StreamReader(), writer)
        async with asyncio.timeout(0.01):
            await stream.aclose()
        writer.transport.abort.assert_called_once()
        writer.wait_closed.assert_not_awaited()
