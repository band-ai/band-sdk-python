from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest

from remote_agent.band.arithmetic import (
    RuntimeDriver,
    replay_turns,
    validate_candidate,
    write_runtime_configuration,
)
from remote_agent.band.serve_programs import assessor
from remote_agent.network import HTTPRoom
from remote_agent.program import Artifact
from remote_agent.service import Service
from remote_agent.store import Store
from remote_agent.types import JsonObject
from tests.contracts.runtime.taskroom import UnknownAckRoom
from tests.paths import CONTRACT_RUNTIME_TESTS


class TestProgramWorker(unittest.TestCase):
    @pytest.mark.contract_isolation
    def test_new_native_arithmetic_revision_runs_independent_program_checks_and_keeps_unknown_sources_closed(
        self,
    ) -> None:
        source = (
            CONTRACT_RUNTIME_TESTS / "fixtures/band/native-studio-arithmetic.band.txt"
        ).read_text()
        pin = hashlib.sha256(source.encode()).hexdigest()
        candidate = {
            "classification": "program",
            "reason": "Bounded arithmetic",
            "handles": ["add", "multiply"],
            "delegates": [],
            "evidence": [],
            "source": files("remote_agent.band")
            .joinpath("arithmetic_program.py")
            .read_text(),
        }
        context = {"history": [], "source": {"sha256": pin, "content": source}}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("remote_agent.band.serve_programs.invoke", return_value=candidate),
        ):
            evaluate = assessor(Path(directory))
            result = evaluate(context, {"id": "qualified", "role": "calculator"})
            self.assertTrue(result["deployable"])
            self.assertGreater(len(result["checks"]), 5)
            self.assertIsNotNone(result["program_sha256"])
            context["source"]["sha256"] = "f" * 64
            self.assertFalse(
                evaluate(context, {"id": "unknown", "role": "calculator"})["deployable"]
            )

    @pytest.mark.contract_linux
    def test_host_relocation_preserves_existing_callback_pins_and_refuses_other_configuration_changes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "service.json"
            original = {
                "agent": {
                    "actor": "Agent:calculator",
                    "provider": {"command": ["python", "/sealed/v2/program_model.py"]},
                    "validator": {
                        "command": ["python", "/sealed/v2/program_validator.py"]
                    },
                },
                "engine": {"command": ["pinned-engine"]},
            }
            write_runtime_configuration(path, original)
            encoded = path.read_bytes()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            relocated = copy.deepcopy(original)
            relocated["agent"]["provider"]["command"][-1] = (
                "/sealed/v4/program_model.py"
            )
            relocated["agent"]["validator"]["command"][-1] = (
                "/sealed/v4/program_validator.py"
            )
            write_runtime_configuration(path, relocated)
            self.assertEqual(path.read_bytes(), encoded)
            self.assertEqual(json.loads(path.read_text()), original)
            relocated["agent"]["actor"] = "Agent:another"
            with self.assertRaisesRegex(
                ValueError, "pinned_runtime_configuration_changed"
            ):
                write_runtime_configuration(path, relocated)
            self.assertEqual(path.read_bytes(), encoded)

    @pytest.mark.contract_isolation
    def test_legacy_assessment_rejects_classification_that_disagrees_with_qualified_coverage(
        self,
    ) -> None:
        source = (
            files("remote_agent.band").joinpath("arithmetic_program.py").read_text()
        )
        partial = source.replace('"mode": "full"', '"mode": "partial"').replace(
            'payload["op"] not in ("add", "multiply")', 'payload["op"] not in ("add",)'
        )
        context = {
            "history": [],
            "source": {
                "sha256": "9b02c7efb74809f8b1192a7bd3002db9c3fdce775cad02ea9db984244036e1a0"
            },
        }
        for classification, candidate_source in (
            ("program", partial),
            ("hybrid", source),
        ):
            with (
                self.subTest(classification=classification),
                tempfile.TemporaryDirectory() as directory,
            ):
                candidate = {
                    "classification": classification,
                    "reason": "Bounded arithmetic",
                    "handles": ["addition"],
                    "delegates": ["multiplication"],
                    "evidence": [],
                    "source": candidate_source,
                }
                evaluate = assessor(
                    Path(directory),
                    invoke_model=lambda *args, candidate=candidate, **kwargs: candidate,
                )
                with self.assertRaisesRegex(ValueError, "assessment_coverage_mismatch"):
                    evaluate(context, {"id": "qualified", "role": "calculator"})

    @pytest.mark.contract_isolation
    def test_legacy_program_start_rejects_partial_coverage_before_creating_a_service(
        self,
    ) -> None:
        source = (
            files("remote_agent.band").joinpath("arithmetic_program.py").read_text()
        )
        partial = source.replace('"mode": "full"', '"mode": "partial"').replace(
            'payload["op"] not in ("add", "multiply")', 'payload["op"] not in ("add",)'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "agent.key"
            key.write_text("k" * 32)
            key.chmod(0o600)
            model = root / "model.json"
            model.write_text(
                json.dumps(
                    {
                        "profile": "band-contract/structured-model/1",
                        "command": [
                            sys.executable,
                            "-c",
                            'raise RuntimeError("unexpected model call")',
                        ],
                        "credentials": {},
                    }
                )
            )
            model.chmod(0o600)
            value = {
                "room": "room",
                "session": "session",
                "actor": "User:owner",
                "request": {
                    "id": "start",
                    "journal": True,
                    "mode": "program",
                    "target_bytes": 100000000,
                    "minimum_steps": 100,
                },
                "context": {
                    "config": {
                        "bindings": {
                            "calculator": "Agent:calculator",
                            "requester": "Agent:requester",
                        }
                    },
                    "source": {
                        "sha256": "9b02c7efb74809f8b1192a7bd3002db9c3fdce775cad02ea9db984244036e1a0",
                        "content": "pinned contract",
                    },
                },
            }
            host = {
                "agent_keys": {"Agent:calculator": str(key)},
                "engine_command": [sys.executable],
                "model_config": str(model),
                "platform_url": "https://band.example",
            }
            with patch(
                "remote_agent.band.arithmetic.Service",
                side_effect=AssertionError("service must not start"),
            ) as service:
                with self.assertRaisesRegex(
                    ValueError, "program_requires_full_coverage"
                ):
                    RuntimeDriver(
                        value,
                        {"role": "calculator", "source": partial},
                        root / "runtime",
                        host_config=host,
                    )
                service.assert_not_called()

    @pytest.mark.contract_isolation
    def test_legacy_assessment_accepts_matching_full_and_partial_coverage(self) -> None:
        source = (
            files("remote_agent.band").joinpath("arithmetic_program.py").read_text()
        )
        partial = source.replace('"mode": "full"', '"mode": "partial"').replace(
            'payload["op"] not in ("add", "multiply")', 'payload["op"] not in ("add",)'
        )
        context = {
            "history": [],
            "source": {
                "sha256": "9b02c7efb74809f8b1192a7bd3002db9c3fdce775cad02ea9db984244036e1a0"
            },
        }
        for classification, candidate_source in (
            ("program", source),
            ("hybrid", partial),
        ):
            with (
                self.subTest(classification=classification),
                tempfile.TemporaryDirectory() as directory,
            ):
                candidate = {
                    "classification": classification,
                    "reason": "Bounded arithmetic",
                    "handles": ["addition"],
                    "delegates": ["unsupported inputs"],
                    "evidence": [],
                    "source": candidate_source,
                }
                result = assessor(
                    Path(directory),
                    invoke_model=lambda *args, candidate=candidate, **kwargs: candidate,
                )(context, {"id": "qualified", "role": "calculator"})
                self.assertTrue(result["deployable"])
                self.assertEqual(result["classification"], classification)
                self.assertIsNotNone(result["program_sha256"])

    @pytest.mark.contract_linux
    def test_legacy_startup_closes_its_service_if_counterparty_connection_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "agent.key"
            key.write_text("k" * 32)
            key.chmod(0o600)
            model = root / "model.json"
            model.write_text(
                json.dumps(
                    {
                        "profile": "band-contract/structured-model/1",
                        "command": [
                            sys.executable,
                            "-c",
                            'raise RuntimeError("unexpected model call")',
                        ],
                        "credentials": {},
                    }
                )
            )
            model.chmod(0o600)
            value = {
                "room": "room",
                "session": "session",
                "actor": "User:owner",
                "request": {
                    "id": "start",
                    "journal": True,
                    "mode": "program",
                    "target_bytes": 100000000,
                    "minimum_steps": 100,
                },
                "context": {
                    "config": {
                        "bindings": {
                            "calculator": "Agent:calculator",
                            "requester": "Agent:requester",
                        }
                    },
                    "source": {
                        "sha256": "9b02c7efb74809f8b1192a7bd3002db9c3fdce775cad02ea9db984244036e1a0",
                        "content": "pinned contract",
                    },
                },
            }
            host = {
                "agent_keys": {
                    "Agent:calculator": str(key),
                    "Agent:requester": str(key),
                },
                "engine_command": [sys.executable],
                "model_config": str(model),
                "platform_url": "https://band.example",
            }
            service = SimpleNamespace(close=Mock())
            with (
                patch(
                    "remote_agent.band.arithmetic.validate_candidate",
                    return_value={"coverage": {"mode": "full"}},
                ),
                patch("remote_agent.band.arithmetic.Service", return_value=service),
                patch(
                    "remote_agent.band.arithmetic.HTTPRoom",
                    side_effect=OSError("temporary connection failure"),
                ),
                self.assertRaisesRegex(OSError, "temporary connection failure"),
            ):
                RuntimeDriver(
                    value,
                    {"role": "calculator", "source": "qualified source"},
                    root / "runtime",
                    host_config=host,
                )
            service.close.assert_called_once_with()

    @pytest.mark.contract_isolation
    def test_quality_validator_refuses_plausible_but_incorrect_arithmetic_and_nonbounded_state(
        self,
    ) -> None:
        source = (
            files("remote_agent.band").joinpath("arithmetic_program.py").read_text()
        )
        with tempfile.TemporaryDirectory() as directory:
            verdict = validate_candidate(source, Path(directory) / "correct")
            self.assertGreater(len(verdict["checks"]), 5)
            with self.assertRaisesRegex(ValueError, "semantic"):
                validate_candidate(
                    source.replace("a + b if", "a - b if"), Path(directory) / "wrong"
                )
            with self.assertRaisesRegex(ValueError, "semantic"):
                validate_candidate(
                    source.replace('state["answered"] + 1', 'state["answered"] + 10'),
                    Path(directory) / "bad-state",
                )

    @pytest.mark.contract_linux
    def test_business_task_reuses_its_staged_wire_request_after_an_unknown_ack(
        self,
    ) -> None:
        room = UnknownAckRoom()
        with (
            tempfile.TemporaryDirectory() as directory,
            Store(Path(directory) / "journal") as store,
        ):
            driver = RuntimeDriver.__new__(RuntimeDriver)
            driver.requester = cast(HTTPRoom, room)
            driver.service = cast(Service, SimpleNamespace(store=store))
            body = {
                "id": "same-id",
                "kind": "question",
                "payload": {"op": "add", "a": 37, "b": 61},
            }
            with self.assertRaisesRegex(ValueError, "lost acknowledgement"):
                driver.task(body)
            self.assertEqual(driver.task(body)["decision"]["status"], "duplicate")
            self.assertEqual(driver.task(body)["decision"]["status"], "duplicate")
            self.assertEqual(len(room.requests), 2)
            self.assertEqual(room.requests[0], room.requests[1])
            self.assertEqual(room.requests[0]["expected_version"], 0)

    @pytest.mark.contract_isolation
    def test_replay_uses_original_versions_and_recorded_model_results_without_room_effects(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first = Artifact.publish(
                files("remote_agent.band")
                .joinpath("arithmetic_program.py")
                .read_text(),
                path,
            )
            second = Artifact.publish(
                files("remote_agent.band").joinpath("arithmetic_program.py").read_text()
                + "\n# version two\n",
                path,
            )
            rows: list[JsonObject] = []
            for n, (artifact, op, a, b) in enumerate(
                ((first, "add", 3, 4), (second, "multiply", 9, 11))
            ):
                rows.append(
                    {
                        "program": artifact.sha256,
                        "producer": "program",
                        "input": {
                            "id": str(n),
                            "kind": "question",
                            "payload": {"op": op, "a": a, "b": b},
                        },
                        "before": {"answered": n},
                        "state": {"answered": n + 1},
                        "message": {
                            "kind": "answer",
                            "payload": {
                                "request": str(n),
                                "value": a + b if op == "add" else a * b,
                            },
                        },
                        "view": {},
                    }
                )
            result = replay_turns(rows, {"answered": 0}, path)
            self.assertEqual(result["state"], {"answered": 2})
            self.assertEqual(len(result["versions"]), 2)
            self.assertEqual(result["published_effects"], 0)
            self.assertEqual(result["model_calls"], 0)
            terminal = {
                "producer": "supervisor",
                "before": {"answered": 2},
                "state": {"answered": 2},
                "message": None,
                "view": {"status": "terminal"},
            }
            self.assertEqual(
                replay_turns(rows + [terminal], {"answered": 0}, path)[
                    "supervisor_turns"
                ],
                1,
            )
            terminal["state"] = {"answered": 3}
            with self.assertRaisesRegex(ValueError, "replay_diverged_terminal_discard"):
                replay_turns(rows + [terminal], {"answered": 0}, path)

            rows[-1]["message"]["payload"]["value"] = 100
            with self.assertRaisesRegex(ValueError, "replay_diverged"):
                replay_turns(rows, {"answered": 0}, path)
