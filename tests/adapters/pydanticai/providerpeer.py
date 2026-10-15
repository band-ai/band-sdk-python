"""Local HTTP peer for the released default OpenAI provider."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ProviderPeer:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.paths: list[str] = []

    def response(self, path: str, request: dict[str, Any]) -> dict[str, Any]:
        self.paths.append(path)
        self.requests.append(request)
        first = len(self.requests) == 1
        if path.endswith("/chat/completions"):
            message = (
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "record",
                                "arguments": '{"note":"hello"}',
                            },
                        }
                    ],
                }
                if first
                else {"role": "assistant", "content": "done"}
            )
            return {
                "id": "chat-1",
                "object": "chat.completion",
                "created": 1,
                "model": request["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if first else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                },
            }
        output = (
            [
                {
                    "type": "function_call",
                    "id": "fc-1",
                    "call_id": "call-1",
                    "name": "record",
                    "arguments": '{"note":"hello"}',
                    "status": "completed",
                }
            ]
            if first
            else [
                {
                    "type": "message",
                    "id": "msg-1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "done", "annotations": []}
                    ],
                }
            ]
        )
        return {
            "id": "resp-1",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": request["model"],
            "output": output,
            "usage": {
                "input_tokens": 7,
                "output_tokens": 3,
                "total_tokens": 10,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        }

    def tool_results(self) -> list[str]:
        request = self.requests[1]
        if "input" in request:
            return [
                item["output"]
                for item in request["input"]
                if item.get("type") == "function_call_output"
            ]
        return [
            item["content"]
            for item in request["messages"]
            if item.get("role") == "tool"
        ]

    @contextmanager
    def serve(self) -> Iterator[str]:
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                request = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                data = json.dumps(peer.response(self.path, request)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"http://127.0.0.1:{server.server_port}/v1"
            finally:
                server.shutdown()
                thread.join()
