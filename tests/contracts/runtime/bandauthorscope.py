from __future__ import annotations

import io
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

from remote_agent.band.authoring import Journal


def http_error(
    status: int = 404,
    body: bytes = b'{"error":"not_found"}',
    content_type: str | None = "application/json",
) -> Any:
    headers = Message()
    if content_type is not None:
        headers["Content-Type"] = content_type
    return HTTPError(
        "https://band.example/job/result", status, "fixture", headers, io.BytesIO(body)
    )


class AuthorScopeFixture(unittest.TestCase):
    def fixture(self, *, legacy: bool = False) -> tuple[Any, ...]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        journal = Journal(root)
        self.addCleanup(journal.close)
        if legacy:
            journal.db.execute(
                "CREATE TABLE studio_jobs(job TEXT, attempt INTEGER, local_id TEXT NOT NULL, body TEXT, acknowledged INTEGER NOT NULL DEFAULT 0, rejected_claim TEXT, PRIMARY KEY(job,attempt))"
            )
            journal.db.commit()
        return root, journal

    def job(self, identifier: Any) -> dict[str, Any]:
        return {
            "id": identifier,
            "attempt": 0,
            "request": {"operation": "generate", "history": []},
        }

    def output(self) -> dict[str, Any]:
        return {
            "source": "Exact completed artifact: café\n",
            "derivation": [],
            "unresolved": [],
        }
