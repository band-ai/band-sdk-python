from __future__ import annotations

from pathlib import Path

import pytest

from remote_agent import operations
from tests.contracts.runtime.hostkey.kit import StoredIdRecoveryFixture


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestStoredIdRecovery(StoredIdRecoveryFixture):
    def test_staged_sent_id_survives_rotation_and_restart(self) -> None:
        agent, event, identifier = self.pending()
        operations.rotate_host_key(self.store.path)
        self.restart(agent, event, identifier)

    def test_staged_sent_id_survives_key_loss_and_restart(self) -> None:
        agent, event, identifier = self.pending()
        Path(str(self.store.path) + ".host-key").unlink()
        self.restart(agent, event, identifier)
        self.assertIn("host_key_recovered", [r["kind"] for r in self.store.records()])
