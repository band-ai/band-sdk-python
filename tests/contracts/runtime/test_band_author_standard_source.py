from __future__ import annotations

import io
import json
import tempfile
import unittest
from email.message import Message
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest
from jsonschema import Draft202012Validator

from remote_agent.band.authoring import (
    Journal,
    author_prompt,
    canonical,
    output_schema,
    process_one,
)
from remote_agent.band.studio import StudioAgent
from tests.paths import CONTRACTS_ROOT


@pytest.mark.contract_linux
class TestAuthorStandardSource(unittest.TestCase):
    def test_complete_package_reaches_packaged_prompt_and_held_retry_keeps_exact_input_and_output(
        self,
    ) -> None:
        fixture = (
            CONTRACTS_ROOT
            / "spec/conformance/case-v2-frontmatter-and-standard-discovery.json"
        )
        case = json.loads(fixture.read_text(encoding="utf-8"))
        source = case["requests"][1]["source"]
        worker_source = canonical(source)
        request = {
            "operation": "refine",
            "goal": "Clarify package guidance while preserving the protocol",
            "history": [
                {
                    "id": "note-1",
                    "role": "User:owner",
                    "text": "Preserve the existing ping and close behavior.",
                }
            ],
            "source": worker_source,
            "range": {
                "from": "2026-01-01T00:00:00.000000Z",
                "to": "2026-01-02T00:00:00.000000Z",
            },
            "previous_questions": [],
        }
        result = {
            "source": case["requests"][0]["source"]["content"],
            "derivation": [{"evidence": "note-1", "rule": "Preserve ping and close"}],
            "unresolved": [],
        }
        queue = [
            {
                "id": "standard-job",
                "attempt": 0,
                "request": request,
                "baseline": None,
                "source": None,
            }
        ]
        local_id = "studio:standard-job:0"
        sent, claims, prompts = [], [], []
        scope_available = False

        def transport(operation: Any, body: Any) -> dict[str, Any]:
            if operation == "claim":
                claims.append(body["id"])
                return {"job": queue.pop(0) if queue else None}
            self.assertEqual(operation, "standard-job/result")
            sent.append(canonical(body))
            if len(sent) == 1:
                raise TimeoutError("completion committed; acknowledgement lost")
            if not scope_available:
                headers = Message()
                headers["Content-Type"] = "application/json"
                raise HTTPError(
                    "https://band.example/standard-job/result",
                    404,
                    "fixture",
                    headers,
                    io.BytesIO(b'{"error":"not_found"}'),
                )
            return {"id": "standard-job", "attempt": 0, "status": "ready"}

        def model(job: Any) -> Any:
            assets = files("remote_agent.band").joinpath("assets")
            skill = assets.joinpath("room-protocol-author.md").read_text(
                encoding="utf-8"
            )
            grammar = assets.joinpath("grammar.md").read_text(encoding="utf-8")
            prompt = author_prompt(grammar, job)
            prompts.append(prompt)
            instructions, data = prompt.split("\nOPERATOR JOB (DATA):\n", 1)
            # Check the actual packaged guidance and its placement without
            # coupling the forwarding contract to the guidance's wording.
            self.assertIn(skill, instructions)
            self.assertIn(grammar, instructions)
            self.assertLess(instructions.index(skill), instructions.index(grammar))
            prompt_job = json.loads(data)
            self.assertEqual(prompt_job, request)
            self.assertEqual(prompt_job["source"], worker_source)
            decoded = json.loads(prompt_job["source"])
            self.assertEqual(decoded, source)
            self.assertEqual(
                decoded["content"].encode("utf-8"), source["content"].encode("utf-8")
            )
            self.assertEqual(
                decoded["body"].encode("utf-8"), source["body"].encode("utf-8")
            )
            Draft202012Validator(output_schema(job)).validate(result)
            return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = Journal(root)
            try:
                StudioAgent(journal, transport).poll()
                stored_input = journal.db.execute(
                    "SELECT input FROM jobs WHERE id=?", (local_id,)
                ).fetchone()[0]
                self.assertEqual(stored_input, canonical(request))
                journal.close()
                journal = Journal(root)
                journal.recover()
                self.assertTrue(process_one(journal, model))
                original_result = journal.result(local_id)
                self.assertEqual(original_result["status"], "complete", original_result)
                self.assertEqual(original_result["result"], result)
                self.assertEqual(len(prompts), 1)

                with self.assertRaises(TimeoutError):
                    StudioAgent(journal, transport).poll()
                staged_body = journal.db.execute(
                    "SELECT body FROM studio_jobs WHERE job=? AND attempt=0",
                    ("standard-job",),
                ).fetchone()[0]
                self.assertEqual(
                    json.loads(staged_body), {"attempt": 0, "result": result}
                )
                journal.close()
                journal = Journal(root)
                journal.recover()
                agent = StudioAgent(journal, transport)
                agent.poll()
                held = [
                    {
                        "job": "standard-job",
                        "attempt": 0,
                        "local_id": local_id,
                        "status": "scope_unavailable",
                        "acknowledged": False,
                    }
                ]
                self.assertEqual(agent.held_completions(), held)
                self.assertEqual(sent, [staged_body, staged_body])
                journal.close()
                journal = Journal(root)
                journal.recover()
                agent = StudioAgent(journal, transport)
                self.assertEqual(agent.held_completions(), held)

                scope_available = True
                claims_before_retry = list(claims)
                self.assertTrue(agent.retry_held("standard-job", 0)["acknowledged"])
                self.assertEqual(claims, claims_before_retry)
                self.assertEqual(sent, [staged_body, staged_body, staged_body])
                self.assertEqual(agent.held_completions(), [])
                self.assertEqual(journal.result(local_id), original_result)
                self.assertEqual(
                    journal.db.execute(
                        "SELECT input FROM jobs WHERE id=?", (local_id,)
                    ).fetchone()[0],
                    stored_input,
                )
                journal.submit(local_id, request)
                with self.assertRaisesRegex(ValueError, "job identity conflict"):
                    journal.submit(local_id, dict(request, source=source["content"]))
                self.assertEqual(
                    journal.db.execute(
                        "SELECT input FROM jobs WHERE id=?", (local_id,)
                    ).fetchone()[0],
                    stored_input,
                )
                self.assertFalse(
                    process_one(
                        journal, lambda _: self.fail("Saved output cannot be resampled")
                    )
                )
                self.assertEqual(len(prompts), 1)
            finally:
                journal.close()
