from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

from remote_agent.transport import ReferenceRoom, TransportError


class TestFactPayloadIdentity(unittest.TestCase):
    def test_json_boolean_is_not_an_authoritative_integer_payload(self) -> None:
        for original, forged in ((1, True), (0, False), ({"n": [1]}, {"n": [True]})):
            with self.subTest(original=original, forged=forged):
                fact = {
                    "id": "q",
                    "kind": "question",
                    "actor": "user",
                    "role": "requester",
                    "payload": original,
                    "recipients": ["agent"],
                }
                room = SimpleNamespace(
                    session="s",
                    view=Mock(return_value={"view": {"facts": {"q": fact}}}),
                )
                event = dict(fact, session="s", payload=forged)
                with self.assertRaises(TransportError):
                    ReferenceRoom.accepted_input(
                        cast(ReferenceRoom, room), event, "agent", 0
                    )
                event["payload"] = original
                normalized = ReferenceRoom.accepted_input(
                    cast(ReferenceRoom, room), event, "agent", 0
                )
                self.assertEqual(type(normalized["payload"]), type(original))
