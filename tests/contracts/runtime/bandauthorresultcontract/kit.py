from __future__ import annotations

import io
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

from remote_agent.band.authoring import Journal

JOB = {
    "operation": "generate",
    "history": [{"id": "m1", "role": "human", "text": "Review"}],
}


def output(**changes: Any) -> dict[str, Any]:
    return dict(
        {
            "source": "typed artifact",
            "derivation": [{"evidence": "m1", "rule": "Review"}],
            "unresolved": [],
        },
        **changes,
    )


def invalid_outputs() -> dict[str, Any]:
    return {
        "empty_source": output(source=""),
        "large_source": output(source="x" * 400001),
        "source_utf8": output(source="😀" * 100000 + "x"),
        "unresolved_count": output(unresolved=["choice"] * 17),
        "empty_choice": output(unresolved=[""]),
        "large_choice": output(unresolved=["x" * 4001]),
        "choice_utf8": output(unresolved=["😀" * 1000 + "x"]),
        "choice_type": output(unresolved=[None]),
        "derivation_count": output(
            derivation=[{"evidence": "m1", "rule": "Review"}] * 257
        ),
        "empty_rule": output(derivation=[{"evidence": "m1", "rule": ""}]),
        "large_rule": output(derivation=[{"evidence": "m1", "rule": "x" * 4001}]),
        "rule_utf8": output(derivation=[{"evidence": "m1", "rule": "😀" * 1000 + "x"}]),
        "rule_type": output(derivation=[{"evidence": "m1", "rule": False}]),
        "evidence_identity": output(
            derivation=[{"evidence": "m1: quotation", "rule": "Review"}]
        ),
        "evidence_type": output(derivation=[{"evidence": [], "rule": "Review"}]),
        "derivation_shape": output(
            derivation=[{"evidence": "m1", "rule": "Review", "extra": 1}]
        ),
        "result_shape": dict(output(), extra=1),
    }


def response_error(
    status: int = 422, body: bytes = b'{"error":"invalid_result"}'
) -> Any:
    headers = Message()
    headers["Content-Type"] = "application/json"
    return HTTPError(
        "https://band.example/job/result", status, "fixture", headers, io.BytesIO(body)
    )


class AuthorResultRecoveryFixture(unittest.TestCase):
    def fixture(self) -> tuple[Any, ...]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        journal = Journal(root)
        self.addCleanup(journal.close)
        return root, journal
