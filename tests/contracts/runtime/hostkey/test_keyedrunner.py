from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest

from remote_agent.storage import canonical, digest
from tests.contracts.runtime.hybrid import HybridFixture


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestKeyedRunner(HybridFixture):
    def test_h7_reply_uses_private_domain_separated_hmac_not_public_prediction(
        self,
    ) -> None:
        agent = self.start()
        event = self.question()
        reply = agent.receive(event)
        identifier = reply["receipt"]["id"]
        prediction = "remote:" + digest(["s", "agent", event["id"], 1])[:40]
        self.assertNotEqual(
            identifier, prediction, "H7: public prediction still preclaims the reply"
        )
        key_path = Path(str(self.store.path) + ".host-key")
        self.assertTrue(key_path.exists())
        key = key_path.read_bytes()
        self.assertTrue(len(key) == 32)
        expected = (
            "remote:"
            + hmac.new(
                key,
                canonical(["band-remote-id/1", "s", "agent", event["id"], 1]),
                hashlib.sha256,
            ).hexdigest()[:40]
        )
        self.assertTrue(
            identifier == expected, "HMAC must use the canonical domain-labelled tuple"
        )
