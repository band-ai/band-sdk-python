from __future__ import annotations

import pytest

from tests.contracts.runtime.durableroom.kit import DurableReplyRoomFixture


@pytest.mark.contract_linux
@pytest.mark.contract_engine
class TestDurableReplyRoom(DurableReplyRoomFixture):
    def test_stated_capabilities_are_pinned_and_restore_needs_the_same_list(
        self,
    ) -> None:
        room, engine, store = self.open(capabilities=["contract.replies/1"])
        self.assertEqual(room.config["capabilities"], ["contract.replies/1"])
        deploy = {
            "id": "d1",
            "session": "s",
            "kind": "deploy",
            "payload": {},
            "expected_version": 0,
        }
        self.assertEqual(room.publish(deploy, "l", self.clock)["status"], "accepted")
        engine.close()
        store.close()
        self.refused("Pinned room configuration changed", capabilities=[])
        self.refused("Pinned room configuration changed")
        room, _, _ = self.open(capabilities=["contract.replies/1"])
        self.assertEqual(room.view("d", self.clock)["view"]["wait"]["head"], "d1")

    def test_a_room_that_states_no_capabilities_refuses_a_reply_contract(self) -> None:
        self.refused("unsupported_extension", capabilities=[])

    def test_unstated_capabilities_keep_the_unpinned_configuration(self) -> None:
        room, _, _ = self.open()
        self.assertNotIn("capabilities", room.config)

    def test_a_reply_room_starts_and_restores_its_open_wait(self) -> None:
        room, engine, store = self.open()
        deploy = {
            "id": "d1",
            "session": "s",
            "kind": "deploy",
            "payload": {},
            "expected_version": 0,
        }
        self.assertEqual(room.publish(deploy, "l", self.clock)["status"], "accepted")
        engine.close()
        store.close()
        room, _, _ = self.open()
        view = room.view("d", self.clock)["view"]
        self.assertEqual((view["state"], view["wait"]["head"]), ("deploying", "d1"))
        reply = {
            "id": "o1",
            "session": "s",
            "kind": "ok",
            "payload": {"request": "d1"},
            "expected_version": 1,
        }
        self.assertEqual(room.publish(reply, "d", self.clock)["state"], "deployed")
