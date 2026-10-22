from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Any


class AuthenticationHandler(BaseHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        observed: list[dict[str, str]],
        legacy_view: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        self.observed = observed
        self.legacy_view = legacy_view
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.observed.append(dict(self.headers.items()))
        identity = {
            "profile": "contract-room/http/1",
            "idempotent": True,
            "identity": {"definition": "A" * 64, "source": "B" * 64, "ready": True},
            "session": "s",
            "actor": "Agent:actor",
            "inbox_profile": "contract-room/inbox/1",
        }
        # Auth-only stub: a legacy view must be a view, not an identity
        # response. Its existing definition avoids an unrelated GET.
        body = json.dumps(
            self.legacy_view if self.path == "/v1/view" else identity
        ).encode()
        self.send_response(200 if self.headers.get("X-API-Key") == "k" * 32 else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        self.do_GET()
