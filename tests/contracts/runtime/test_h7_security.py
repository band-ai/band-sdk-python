from __future__ import annotations

import hmac
from pathlib import Path

import pytest

from remote_agent import operations
from remote_agent.hostkey import HostKey
from remote_agent.storage import canonical, digest
from tests.contracts.runtime.h7security import H7SecurityFixture


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestH7Security(H7SecurityFixture):
    def test_peer_preclaiming_every_old_prediction_cannot_exhaust_runner_attempts(
        self,
    ) -> None:
        agent = self.start()
        predictions = {
            "remote:" + digest(["s", "agent", "input:q", attempt])[:40]
            for attempt in range(1, 4)
        }
        for identifier in sorted(predictions):
            accepted = self.room.publish(
                {"id": identifier, "session": "s", "kind": "notice", "payload": {}},
                "peer",
                0,
            )
            self.assertEqual(accepted["status"], "accepted")
        result = agent.receive(self.question())
        self.assertEqual(
            result["status"], "completed", "Predictable preclaims blocked delivery"
        )
        self.assertNotIn(result["receipt"]["id"], predictions)
        self.assertEqual(result["receipt"]["actor"], "agent")
        self.assertEqual(result["receipt"]["payload"]["text"], "pong")
        self.assertEqual(self.store.get("turns"), 1)

    def test_separate_hosts_produce_different_ids_for_the_same_public_tuple(
        self,
    ) -> None:
        first = self.start()
        first_id = first.receive(self.question())["receipt"]["id"]
        first.close()
        self.store.close()
        self.store = self.open_store("other-host.sqlite3")
        self.room = self.new_room()
        second_id = self.start().receive(self.question())["receipt"]["id"]
        self.assertNotEqual(
            first_id, second_id, "Two hosts shared a predictable response ID"
        )

    def test_same_key_restart_preserves_domain_separated_derivation(self) -> None:
        agent = self.start()
        key_class = HostKey
        identifier = key_class(self.store).identifier("s", "agent", "input:q", 1)
        key_path = Path(str(self.store.path) + ".host-key")
        # Do not use assertEqual on key bytes: failure output must not reveal them.
        expected = (
            "remote:"
            + hmac.new(
                key_path.read_bytes(),
                canonical(["band-remote-id/1", "s", "agent", "input:q", 1]),
                "sha256",
            ).hexdigest()[:40]
        )
        self.assertEqual(identifier, expected)
        self.assertEqual(agent.receive(self.question())["receipt"]["id"], identifier)
        agent.close()
        self.store.close()
        self.store = self.open_store("agent.sqlite3")
        self.start()
        self.assertEqual(
            key_class(self.store).identifier("s", "agent", "input:q", 1), identifier
        )

    def test_payload_change_does_not_change_id_with_same_host_key_and_public_tuple(
        self,
    ) -> None:
        agent = self.start()
        archive = self.path / "initial.backup"
        operations.backup(self.store.path, archive)
        first = agent.receive(self.question())
        agent.close()
        self.store.close()
        operations.restore(archive, self.path / "variant.sqlite3")
        self.store = self.open_store("variant.sqlite3")
        self.room = self.new_room()
        second = self.start().receive(self.question("requires language judgment"))
        self.assertNotEqual(first["receipt"]["payload"], second["receipt"]["payload"])
        self.assertEqual(first["receipt"]["id"], second["receipt"]["id"])

    def test_rotation_restart_reconciles_the_persisted_sent_id_without_new_turn(
        self,
    ) -> None:
        self.staged_recovery("rotate")

    def test_lost_key_restart_reconciles_the_persisted_sent_id_without_new_turn(
        self,
    ) -> None:
        self.staged_recovery("lost")

    def test_presend_crash_rotation_restart_publishes_exact_staged_envelope(
        self,
    ) -> None:
        self.staged_unsent_recovery("rotate")

    def test_presend_crash_key_loss_restart_publishes_exact_staged_envelope(
        self,
    ) -> None:
        self.staged_unsent_recovery("lost")
