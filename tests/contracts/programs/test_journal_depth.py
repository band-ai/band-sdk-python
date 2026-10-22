"""Read real retained pages inside the native program-host response envelope."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.contracts.programs import ContractProgramHostClient
from tests.contracts.journaldepth import (
    JournalPeer,
    assert_journal_request,
    record_callback,
    record_private_value,
)


@pytest.mark.contract_linux
class TestProgramJournalDepth:
    @pytest.mark.parametrize("levels", [125, 126])
    async def test_host_journal_preserves_accepted_callback_state(
        self, tmp_path: Path, levels: int
    ) -> None:
        recording = record_callback(tmp_path, levels)
        peer = JournalPeer(recording.page)
        async with (
            peer.client() as http,
            ContractProgramHostClient(
                peer.connection, scope=peer.scope, http=http
            ) as client,
        ):
            response = await client.journal()

        assert response["journal_page"] == recording.page
        assert_journal_request(peer)

    async def test_host_journal_preserves_json_accepted_by_private_storage(
        self, tmp_path: Path
    ) -> None:
        recording = record_private_value(tmp_path)
        peer = JournalPeer(recording.page)
        async with (
            peer.client() as http,
            ContractProgramHostClient(
                peer.connection, scope=peer.scope, http=http
            ) as client,
        ):
            response = await client.journal()

        assert response["journal_page"] == recording.page
        assert_journal_request(peer)
