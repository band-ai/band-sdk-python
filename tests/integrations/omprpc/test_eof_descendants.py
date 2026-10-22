"""Cleanup discovers descendants created by an owned helper after native stdin EOF."""

from __future__ import annotations

import asyncio

import pytest

from tests.integrations.omprpc.kit import (
    ProcessGate,
    descendant_liveness,
    hold_after_stdin_closed,
    is_descendant,
    job_member_pids,
    process_is_alive,
    session_ids,
    start_eof_client,
    unrelated_gate_child,
)
from tests.ostable import skip_unless_os


class TestEofDescendants:
    @pytest.mark.parametrize("stdio", ["inherit", "discard"])
    async def test_close_reaps_a_detached_child_created_after_stdin_eof(
        self,
        process_gate: ProcessGate,
        stdio: str,
    ) -> None:
        client, helper, root = await start_eof_client(process_gate, stdio=stdio)
        async with unrelated_gate_child(process_gate) as unrelated:
            try:
                async with hold_after_stdin_closed(client) as closing:
                    late_child = await process_gate.connection()
                    assert root.returncode is not None
                    assert is_descendant(late_child.process, helper.process)
                    assert await descendant_liveness([helper, late_child]) == [
                        True,
                        True,
                    ]
                    assert await asyncio.to_thread(process_is_alive, unrelated.process)
                    assert not closing.done()
                assert await descendant_liveness([helper, late_child]) == [False, False]
                assert await asyncio.to_thread(process_is_alive, unrelated.process)
            finally:
                await client.close()

    @pytest.mark.parametrize("accepted_on", [("posix",)])
    async def test_eof_late_child_is_its_own_session(
        self, process_gate: ProcessGate, accepted_on: tuple[str, ...]
    ) -> None:
        skip_unless_os(accepted_on)
        client, helper, root = await start_eof_client(process_gate, stdio="discard")
        try:
            async with hold_after_stdin_closed(client):
                late_child = await process_gate.connection()
                ids = session_ids([late_child.process.pid])
                assert ids == [late_child.process.pid]
                assert ids[0] != root.pid
                assert helper.process.pid != late_child.process.pid
        finally:
            await client.close()

    @pytest.mark.parametrize("accepted_on", [("nt",)])
    async def test_eof_late_child_is_a_job_member(
        self, process_gate: ProcessGate, accepted_on: tuple[str, ...]
    ) -> None:
        skip_unless_os(accepted_on)
        client, helper, _root = await start_eof_client(process_gate, stdio="discard")
        try:
            async with hold_after_stdin_closed(client):
                late_child = await process_gate.connection()
                members = job_member_pids(client)
                assert helper.process.pid in members
                assert late_child.process.pid in members
        finally:
            await client.close()
