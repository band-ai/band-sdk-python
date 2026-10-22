from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler
from typing import Any


class AuthorCompletionHandler(BaseHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        credential: str,
        job: dict[str, Any],
        responses: list[bytes],
        completed: threading.Event,
        first_completion: threading.Event,
        claimed: threading.Event,
        **kwargs: Any,
    ) -> None:
        self.credential = credential
        self.job = job
        self.completions = responses
        self.completed = completed
        self.first_completion = first_completion
        self.claimed = claimed
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        if self.headers.get("x-api-key") != self.credential:
            self.send_error(403)
            return
        length = int(self.headers["content-length"])
        raw = self.rfile.read(length)
        if self.path == "/api/v1/agent/contracts/authoring/claim":
            result = {"job": None if self.claimed.is_set() else self.job}
            self.claimed.set()
        elif self.path == "/api/v1/agent/contracts/authoring/job-one/result":
            self.completions.append(raw)
            if len(self.completions) == 1:
                # The authority committed this result, but its acknowledgement
                # never reached the worker. Stop and restart that exact worker.
                self.first_completion.set()
                self.close_connection = True
                return
            self.completed.set()
            result = {"id": "job-one", "status": "ready"}
        else:
            self.send_error(404)
            return
        data = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
