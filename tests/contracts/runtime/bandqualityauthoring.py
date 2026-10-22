from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from remote_agent.band.serve_programs import assessor
from tests.contracts.runtime.bandrolequality import SOURCE
from tests.contracts.runtime.settings import runtime_settings
from tests.contracts.runtime.timers import TimerClock
from tests.paths import CONTRACT_RUNTIME_TESTS, CONTRACTS_ROOT


def example_plan() -> dict[str, Any]:
    state = {"processed": 0, "mode": "ascii"}

    def proposal(role: Any, identifier: Any, kind: Any, payload: Any) -> dict[str, Any]:
        return {
            "role": role,
            "id": identifier,
            "kind": kind,
            "payload_json": json.dumps(payload),
        }

    def case(name: Any, category: Any, identifier: Any, text: Any) -> dict[str, Any]:
        return {
            "name": name,
            "category": category,
            "trace": [proposal("requester", identifier, "normalize", {"text": text})],
            "input_id": identifier,
            "state_json": json.dumps(state),
            "expected_json": json.dumps(
                {
                    "type": "handled",
                    "state": dict(state, processed=1),
                    "message": {
                        "kind": "normalized",
                        "payload": {"request": identifier, "text": text.upper()},
                    },
                }
            ),
        }

    common = case(
        "withheld spacing and punctuation oracle",
        "common",
        "quality-first",
        "  Band 42!  ",
    )
    boundary = case(
        "withheld maximum ASCII byte boundary", "boundary", "quality-boundary", "a" * 80
    )
    terminal = case(
        "stale accepted input after explicit finish",
        "terminal",
        "quality-terminal",
        "Band",
    )
    terminal["trace"].extend(
        [
            proposal(
                "normalizer",
                "quality-answer",
                "normalized",
                {"request": "quality-terminal", "text": "BAND"},
            ),
            proposal("requester", "quality-finish", "finish", {}),
        ]
    )
    terminal["state_json"] = json.dumps(dict(state, processed=4))
    terminal["expected_json"] = json.dumps(
        {"type": "handled", "state": dict(state, processed=4), "message": None}
    )
    return {
        "classification": "program",
        "reason": "ASCII case conversion has an exact procedure.",
        "handles": ["bounded ASCII case conversion"],
        "delegates": [],
        "evidence": [],
        "state_version": "normalization/1",
        "initial_json": json.dumps(state),
        "cases": [common, boundary, terminal],
    }


MIXED_SOURCE = """from remote_agent.sdk import Delegate, Handled, Message
STATE_VERSION = "mixed/1"
COVERAGE = {"mode":"partial","handles":["bounded integer sums"],"delegates":["faithful language translation"]}
def initial(view): return {"completed":0}
def handle(event,state,context):
    if context.view.get("status") == "terminal": return Handled(state)
    payload=event.get("payload",{})
    if (event.get("kind") == "add" and set(payload) == {"a","b"}
        and all(type(payload[key]) is int and 0 <= payload[key] <= 1000 for key in ("a","b"))):
        updated={"completed":state["completed"]+1}
        context.checkpoint(updated)
        return Handled(updated,Message("added",{"request":event["id"],"value":payload["a"]+payload["b"]}))
    return Delegate("This input requires language interpretation",scope="turn")
"""


def mixed_plan() -> dict[str, Any]:
    def step(role: Any, identifier: Any, kind: Any, payload: Any) -> dict[str, Any]:
        return {
            "role": role,
            "id": identifier,
            "kind": kind,
            "payload_json": json.dumps(payload),
        }

    def addition(
        name: Any, category: Any, identifier: Any, a: Any, b: Any, count: Any
    ) -> dict[str, Any]:
        return {
            "name": name,
            "category": category,
            "trace": [step("requester", identifier, "add", {"a": a, "b": b})],
            "input_id": identifier,
            "state_json": json.dumps({"completed": count}),
            "expected_json": json.dumps(
                {
                    "type": "handled",
                    "state": {"completed": count + 1},
                    "message": {
                        "kind": "added",
                        "payload": {"request": identifier, "value": a + b},
                    },
                }
            ),
        }

    common = addition("ordinary sum", "common", "sum-first", 2, 3, 0)
    boundary = addition(
        "inclusive operand bounds", "boundary", "sum-boundary", 1000, 1000, 4
    )
    judgment = {
        "name": "faithful translation delegates",
        "category": "judgment",
        "trace": [
            step(
                "requester",
                "words-first",
                "translate",
                {"text": "bonjour", "language": "English"},
            )
        ],
        "input_id": "words-first",
        "state_json": '{"completed":1}',
        "expected_json": '{"type":"delegate"}',
    }
    terminal = addition("no reply after finish", "terminal", "sum-last", 1, 2, 3)
    terminal["trace"] += [
        step("assistant", "sum-answer", "added", {"request": "sum-last", "value": 3}),
        step("requester", "finish-last", "finish", {}),
    ]
    terminal["expected_json"] = json.dumps(
        {"type": "handled", "state": {"completed": 3}, "message": None}
    )
    return {
        "classification": "hybrid",
        "reason": "Exact sums are deterministic; faithful translation needs interpretation.",
        "handles": ["bounded integer sums"],
        "delegates": ["translation"],
        "evidence": [],
        "state_version": "mixed/1",
        "initial_json": '{"completed":0}',
        "cases": [common, boundary, judgment, terminal],
    }


class QualityAuthoringFixture(unittest.TestCase):
    @staticmethod
    def reference_engine() -> Any:
        repo = CONTRACTS_ROOT
        command = [shutil.which("elixir"), "--erl", "+S 2:2"]
        for ebin in sorted(
            (repo / "engines/band-v1/contract-lab/_build/dev/lib").glob("*/ebin")
        ):
            command += ["-pa", str(ebin)]
        command += [
            str(repo / "engines/band-v1/contract-lab/examples/engine_endpoint.exs")
        ]
        return command

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = (
            CONTRACT_RUNTIME_TESTS / "fixtures/band/label-normalization.band.txt"
        ).read_text()
        self.context = {
            "source": {
                "profile": "band-contract/file-source/1",
                "format": "frontmatter",
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
                "content": source,
            },
            "config": {
                "bindings": {
                    "requester": "Agent:requester",
                    "normalizer": "Agent:normalizer",
                }
            },
            "history": [],
        }
        self.request = {"id": "automatic-qualification", "role": "normalizer"}
        self.engine_command = (
            runtime_settings().band_test_engine_command or self.reference_engine()
        )
        self.candidate = {
            "classification": "program",
            "reason": "A complete deterministic callback.",
            "handles": ["ASCII normalization"],
            "delegates": [],
            "evidence": [],
            "source": SOURCE,
        }

    def assess(
        self,
        plan: Any = None,
        candidate: Any = None,
        correction: Any = None,
        **settings: Any,
    ) -> tuple[Any, ...]:
        prompts = []
        self.timeouts = []
        self.timeout_limits = []
        self.clock = TimerClock()

        def invoke(path: Any, prompt: Any, schema: Any, **options: Any) -> Any:
            prompts.append(prompt)
            self.timeouts.append(options["timeout"])
            self.timeout_limits.append(options.get("timeout_limit"))
            self.clock.advance(7)
            if len(prompts) == 1:
                self.assertNotIn(SOURCE, prompt)
                return copy.deepcopy(plan if plan is not None else example_plan())
            policy = self.root / self.request["id"] / "quality-author" / "quality.json"
            self.assertTrue(
                policy.exists(),
                "Independent examples must be frozen before candidate generation",
            )
            self.assertNotIn("withheld spacing and punctuation oracle", prompt)
            if len(prompts) == 3:
                self.assertIsNotNone(correction)
                return copy.deepcopy(correction)
            self.assertEqual(len(prompts), 2)
            return copy.deepcopy(candidate if candidate is not None else self.candidate)

        controlled_time = SimpleNamespace(monotonic=self.clock.monotonic)
        with (
            patch("remote_agent.band.serve_programs.invoke", side_effect=invoke),
            patch("remote_agent.band.serve_programs.time", controlled_time),
            patch("remote_agent.band.qualification.time", controlled_time),
        ):
            result = assessor(
                self.root,
                auto_qualification=True,
                engine_command=self.engine_command,
                **settings,
            )(self.context, self.request)
        return result, prompts
