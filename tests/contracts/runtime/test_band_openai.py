from __future__ import annotations

import base64
import json
import unittest
from typing import Any

from remote_agent.band.openai_model import execute


class TestBandOpenAICommand(unittest.TestCase):
    def test_request_has_no_tools_and_requires_a_complete_single_json_message(
        self,
    ) -> None:
        observed = []
        response = {
            "profile": "remote-agent/openai-result/1",
            "status": 200,
            "request_id": "test",
            "body": base64.b64encode(
                json.dumps(
                    {
                        "status": "completed",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": '{"source":"contract"}',
                                    }
                                ],
                            }
                        ],
                    }
                ).encode()
            ).decode(),
            "complete": True,
            "failure": None,
            "uncertain": False,
            "redacted": False,
        }

        def send(value: Any) -> Any:
            observed.append(value)
            return response

        request = {
            "profile": "band-contract/structured-model-request/1",
            "prompt": "room history",
            "schema": {"type": "object", "properties": {"source": {"type": "string"}}},
        }
        self.assertEqual(
            execute(request, model="configured-test-model", send=send),
            {"source": "contract"},
        )
        body = observed[0]["body"]
        self.assertEqual(body["text"], {"format": {"type": "json_object"}})
        self.assertNotIn("tools", body)
        self.assertIs(body["store"], False)
        self.assertEqual(json.loads(body["input"])["prompt"], "room history")
        for change in [
            {"complete": False},
            {"uncertain": True},
            {"redacted": True},
            {"status": 503},
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                execute(
                    request,
                    model="configured-test-model",
                    send=lambda _, *, change=change: dict(response, **change),
                )
