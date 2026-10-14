"""HTTP/SSE peer for exercising the real OpenAI Responses client and tool loop."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx
from openai import AsyncOpenAI
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from band.testing import ErrorTurn, ScriptedTurn, TextTurn, ToolTurn


class ResponsesPeer:
    """Serve scripted provider responses while recording the serialized requests."""

    CALL_ID = "peer-call-1"
    INPUT_TOKENS = 7
    OUTPUT_TOKENS = 3

    def __init__(self, turns: Sequence[ScriptedTurn]) -> None:
        self.turns = iter(turns)
        self.requests: list[dict[str, Any]] = []
        self.paths: list[str] = []
        self.app = Starlette(
            routes=[Route("/v1/responses", self.respond, methods=["POST"])]
        )

    def client(self, **kwargs: Any) -> AsyncOpenAI:
        """Keep request construction and SSE decoding in the released OpenAI SDK."""
        return AsyncOpenAI(
            **kwargs,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app)),
        )

    async def respond(self, request: Request) -> Response:
        self.paths.append(request.url.path)
        self.requests.append(await request.json())
        events: list[dict[str, Any]] = []
        match next(self.turns):
            case ToolTurn(name=name, args=args):
                item_id = "fc-1"
                call_id = (
                    self.CALL_ID
                    if len(self.requests) == 1
                    else f"{self.CALL_ID}-{len(self.requests)}"
                )
                events.extend(
                    [
                        {
                            "type": "response.output_item.added",
                            "output_index": 0,
                            "item": {
                                "type": "function_call",
                                "id": item_id,
                                "call_id": call_id,
                                "name": name,
                                "arguments": "",
                                "status": "in_progress",
                            },
                        },
                        {
                            "type": "response.function_call_arguments.done",
                            "item_id": item_id,
                            "output_index": 0,
                            "arguments": json.dumps(args),
                        },
                    ]
                )
            case TextTurn(text=text):
                events.append(
                    {
                        "type": "response.output_text.delta",
                        "item_id": "msg-1",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": text,
                    }
                )
            case ErrorTurn(error=error):
                events.append(
                    {
                        "type": "response.failed",
                        "response": {
                            "error": {"code": "server_error", "message": str(error)}
                        },
                    }
                )
        if not any(event["type"] == "response.failed" for event in events):
            events.append(
                {
                    "type": "response.completed",
                    "response": {
                        "usage": {
                            "input_tokens": self.INPUT_TOKENS,
                            "output_tokens": self.OUTPUT_TOKENS,
                            "total_tokens": self.INPUT_TOKENS + self.OUTPUT_TOKENS,
                            "input_tokens_details": {"cached_tokens": 0},
                            "output_tokens_details": {"reasoning_tokens": 0},
                        }
                    },
                }
            )
        frames = "".join(
            f"event: {event['type']}\ndata: {json.dumps({**event, 'sequence_number': index})}\n\n"
            for index, event in enumerate(events)
        )
        return Response(frames, media_type="text/event-stream")

    def tool_outputs(self, request_index: int) -> list[dict[str, Any]]:
        """Function results sent back to the provider on a subsequent request."""
        return [
            item
            for item in self.requests[request_index]["input"]
            if item.get("type") == "function_call_output"
        ]
