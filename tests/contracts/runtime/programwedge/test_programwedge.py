from __future__ import annotations

import logging

import pytest

import tests.contracts.runtime.program as program_tests
from remote_agent.program import Artifact, Program, ProgramError
from tests.contracts.runtime import hybrid
from tests.contracts.runtime.programwedge.kit import (
    BAD_DELEGATE,
    BAD_HANDLED,
    VALID_DESCRIBE,
    ProgramWedgeFixture,
    raw_describe,
)

logger = logging.getLogger(__name__)


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestProgramWedge(ProgramWedgeFixture):
    def test_near_recursion_limit_state_falls_back_without_wedging_recovery(
        self,
    ) -> None:
        # Raw output avoids the worker's own recursive encoder as well as its ABI checks.
        outcomes = []
        for depth in (960, 965, 970, 972, 974, 975, 976, 977, 978, 979):
            with self.subTest(depth=depth):
                state = '{"nested":' + "[" * depth + "0" + "]" * depth + "}"
                encoded = (
                    '{"type":"handled","state":'
                    + state
                    + ',"message":{"kind":"answer","payload":{"request":'
                ).encode()
                source = program_tests.SOURCE + (
                    "\ndef handle(event, state, context):\n"
                    "    import os, json\n    os.write(1, "
                    + repr(encoded)
                    + " + json.dumps(event['id']).encode() + b',\"text\":\"pong\"}}}')\n    os._exit(0)\n"
                )
                agent = hybrid.HybridFixture.start(self, source, max_repairs=0)
                result = agent.receive(self.question())
                recovered = agent.recover()
                outcomes.append(
                    (
                        depth,
                        result["status"],
                        result.get("producer"),
                        recovered["status"],
                    )
                )
                logger.debug("Depth outcome: %s", outcomes[-1])
                # Each depth gets an independent pinned configuration and store.
                if depth != 979:
                    agent.close()
                    self.store.close()
                    self.setUp()
        self.assertTrue(
            all(row[1:] == ("completed", "llm", "idle") for row in outcomes), outcomes
        )
        self.assertIsNone(self.store.get("execution"))

    def test_raw_off_schema_handled_reply_does_not_pin_the_turn(self) -> None:
        self.check_raw_reply(BAD_HANDLED)

    def test_raw_incomplete_delegate_does_not_pin_the_turn(self) -> None:
        self.check_raw_reply(BAD_DELEGATE)

    def test_raw_handled_extra_key_uses_llm_fallback(self) -> None:
        self.check_raw_reply(
            {
                "type": "handled",
                "state": {},
                "message": None,
                "snapshot": None,
                "unexpected": True,
            }
        )

    def test_raw_delegate_extra_key_uses_llm_fallback(self) -> None:
        self.check_raw_reply(
            {
                "type": "delegate",
                "scope": "turn",
                "repair": False,
                "reason": "judgment",
                "unexpected": True,
            }
        )

    def test_raw_invalid_describe_cannot_initialize_the_agent(self) -> None:
        with self.assertRaises(ProgramError):
            hybrid.HybridFixture.start(
                self, raw_describe(dict(VALID_DESCRIBE, unexpected=True))
            )
        self.assertIsNone(self.store.get("config"))
        self.assertFalse(any(r["kind"] == "initialize" for r in self.store.records()))

    def test_raw_invalid_candidate_metadata_fails_before_quality_validator(
        self,
    ) -> None:
        calls = []
        agent = hybrid.HybridFixture.start(
            self,
            validator=lambda candidate, bundle: (
                calls.append("validate") or {"ok": True, "checks": ["fixture"]}
            ),
        )
        value = dict(
            VALID_DESCRIBE, coverage=dict(VALID_DESCRIBE["coverage"], mode="unknown")
        )
        candidate = Program(
            Artifact.publish(raw_describe(value), self.path / "artifacts")
        )
        with self.assertRaises(ProgramError):
            agent._validate_candidate(candidate, {"fault": None}, [])
        self.assertEqual(calls, [])
        self.assertEqual(self.store.get("program"), self.initial.to_dict())

    def test_extra_model_reply_key_is_rejected_before_room_publication(self) -> None:
        original = self.provider.respond
        self.provider.respond = lambda bundle: dict(original(bundle), unexpected=True)
        agent = hybrid.HybridFixture.start(self, max_repairs=0, max_attempts=2)
        result = agent.receive(self.question("needs judgment"))
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.store.get("turns"), 0)
        self.assertEqual(self.store.get("state"), {"count": 0})
        self.assertEqual(self.room.view("agent", 0)["view"]["version"], 1)
        self.assertEqual(
            len([r for r in self.store.records() if r["kind"] == "llm_rejected"]), 2
        )
