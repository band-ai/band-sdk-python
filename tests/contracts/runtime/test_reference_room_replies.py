from __future__ import annotations

import pytest

from remote_agent.transport import ReferenceRoom, TransportError
from tests.contracts.runtime.referenceroomreplies import ReferenceReplyRoomFixture


@pytest.mark.contract_linux
@pytest.mark.contract_engine
class TestReferenceReplyRoom(ReferenceReplyRoomFixture):
    def test_a_reference_room_hosts_a_reply_contract_and_its_wait(self) -> None:
        room = ReferenceRoom(
            self.engine, session="s", bindings={"lead": "l", "deployer": "d"}
        )
        deploy = {
            "id": "d1",
            "session": "s",
            "kind": "deploy",
            "payload": {},
            "expected_version": 0,
        }
        self.assertEqual(room.publish(deploy, "l", 0)["status"], "accepted")
        view = room.view("d", 0)["view"]
        self.assertEqual((view["state"], view["wait"]["head"]), ("deploying", "d1"))

    def test_a_reference_room_that_states_no_capabilities_refuses_a_reply_contract(
        self,
    ) -> None:
        with self.assertRaisesRegex(TransportError, "unsupported_extension"):
            ReferenceRoom(
                self.engine,
                session="s",
                bindings={"lead": "l", "deployer": "d"},
                capabilities=[],
            )
