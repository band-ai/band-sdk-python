"""Keep an old completion durable across later claims and local restarts."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from band.contracts.wire import JsonObject
from remote_agent.band.authoring import Journal, process_one
from remote_agent.band.studio import StudioAgent
from tests.contracts.author.retry import NEXT_CLAIM, RetryPeer


@pytest.mark.contract_linux
class TestAuthorAcknowledgement:
    @settings(max_examples=20, deadline=None)
    @example(actions=["claim", "restart", "retry", "retry"])
    @given(
        actions=st.lists(st.sampled_from(["claim", "restart", "retry"]), max_size=12)
    )
    def test_claim_restart_and_retry_sequences_keep_original_completion(
        self, actions: list[str]
    ) -> None:
        peer = RetryPeer(queued=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = Journal(root)
            try:
                agent = StudioAgent(journal, peer.native)
                agent.poll()
                process_one(
                    journal,
                    lambda _: {"source": "invalid", "derivation": [], "unresolved": []},
                )
                with pytest.raises(TimeoutError, match="response lost"):
                    agent.poll()
                original = journal.db.execute(
                    "SELECT body FROM studio_jobs WHERE attempt=0"
                ).fetchone()[0]
                acknowledged = False
                deliveries = 1
                for action in [*actions, "retry"]:
                    match action:
                        case "claim":
                            if peer.status == "queued":
                                peer.native("claim", {"id": NEXT_CLAIM})
                        case "restart":
                            journal.close()
                            journal = Journal(root)
                            agent = StudioAgent(journal, peer.native)
                        case "retry":
                            agent.poll()
                            deliveries += int(not acknowledged)
                            acknowledged = True
                    saved, ack = journal.db.execute(
                        "SELECT body,acknowledged FROM studio_jobs WHERE attempt=0"
                    ).fetchone()
                    assert saved == original
                    assert bool(ack) == acknowledged
                    assert len(peer.submissions) == deliveries
                    assert peer.submissions == [peer.receipt] * deliveries
                    assert (
                        journal.result(f"studio:{peer.job}:0")["status"] == "complete"
                    )
                assert acknowledged
            finally:
                journal.close()

    @pytest.mark.parametrize("current_attempt", [0, 1])
    def test_rejected_claim_does_not_accept_a_running_projection(
        self, tmp_path: Path, current_attempt: int
    ) -> None:
        def transport(operation: str, body: JsonObject) -> JsonObject:
            if operation == "claim":
                return {
                    "job": {
                        "id": "invalid-job",
                        "attempt": 0,
                        "request": {"operation": "unsupported", "history": []},
                    }
                }
            assert operation == "invalid-job/result"
            assert body["attempt"] == 0
            assert body["held"] is True
            return {
                "id": "invalid-job",
                "attempt": current_attempt,
                "status": "running",
            }

        journal = Journal(tmp_path)
        try:
            agent = StudioAgent(journal, transport)
            agent.poll()
            claim = journal.db.execute("SELECT id FROM studio_claim").fetchone()[0]
            assert claim is not None
            with pytest.raises(ValueError, match="acknowledgement"):
                agent.poll()
            assert (
                journal.db.execute("SELECT id FROM studio_claim").fetchone()[0] == claim
            )
            assert (
                journal.db.execute("SELECT acknowledged FROM studio_jobs").fetchone()[0]
                == 0
            )
        finally:
            journal.close()
