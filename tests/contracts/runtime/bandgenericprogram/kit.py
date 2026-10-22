from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from remote_agent.band.serve_programs import assessor
from tests.contracts.runtime.bandrolequality import SOURCE, policy


class GenericAssessmentFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        content = "Normalize bounded ASCII labels. Preserve spacing and punctuation; uppercase letters. Role: normalizer."
        self.context = {
            "source": {
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "content": content,
            },
            "config": {"bindings": {"normalizer": "Agent:n", "requester": "Agent:r"}},
            "history": [{"id": "accepted-1", "kind": "normalized"}],
        }
        self.quality = policy()
        self.quality["source_sha256"] = self.context["source"]["sha256"]
        self.candidate = {
            "classification": "program",
            "reason": "Exact bounded normalization",
            "handles": ["ASCII labels"],
            "delegates": [],
            "evidence": ["accepted-1"],
            "source": SOURCE,
        }

    def assess(self, *, candidate: Any = None, policies: Any = None) -> tuple[Any, ...]:
        with patch(
            "remote_agent.band.serve_programs.invoke",
            return_value=candidate or self.candidate,
        ) as invoke:
            evaluate = assessor(
                self.root,
                quality_policies=[self.quality] if policies is None else policies,
            )
            result = evaluate(
                self.context, {"id": "new-assessment", "role": "normalizer"}
            )
            return result, invoke.call_args.args[1]
