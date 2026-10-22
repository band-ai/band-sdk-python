"""Read a real program-host journal response through the portable SDK client."""

from __future__ import annotations

import asyncio

import pytest

from remote_agent.band.host import ProgramHost
from tests.contracts.runtime.historydepth import HistoryDepthFixture, container_depth
from tests.contracts.runtime.historyhost import (
    HostJournalPeer,
    StoredParticipant,
    read_host,
    unused_assessor,
    unused_factory,
)


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestHistoryHostDepth(HistoryDepthFixture):
    def test_public_host_client_reads_the_real_history_response(self) -> None:
        self.history_record()
        expected = self.store.search(kind="model_input")
        host = ProgramHost(
            self.path / "program-host",
            assessor=unused_assessor,
            factory=unused_factory,
            authors=[],
        )
        self.addCleanup(host.close)
        host.instances["room/s"] = StoredParticipant(self.store)
        peer = HostJournalPeer(host)
        response = asyncio.run(read_host(peer))
        self.assertEqual(response, peer.response)
        self.assertEqual(response["journal_page"], expected)
        self.assertEqual(container_depth(response), 141)
        self.assertEqual(
            peer.requests,
            [
                {
                    "profile": "band-contract/program-host-request/1",
                    "room": "room",
                    "session": "s",
                    "actor": "User:owner",
                    "context": {},
                    "operation": "journal",
                    "request": {
                        "after": 0,
                        "limit": 50,
                        "kind": "model_input",
                        "query": "",
                    },
                }
            ],
        )
        self.assertEqual(self.model.calls, ["wake", "respond", "respond"])
