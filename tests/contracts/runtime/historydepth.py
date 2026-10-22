"""Create deep records through real controller callbacks and history queries."""

from __future__ import annotations

from typing import Any

from remote_agent.protocol import decode
from remote_agent.storage import canonical
from remote_agent.types import JsonObject
from tests.contracts.runtime.hybrid import HybridFixture, ScriptedModel
from tests.contracts.runtime.required import require_value
from tests.contracts.runtime.setql8 import nested


def container_depth(value: object) -> int:
    """Measure the actual record envelope independently of decoder policy."""
    maximum = 0
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, (dict, list)):
            depth += 1
            maximum = max(maximum, depth)
            children = item.values() if isinstance(item, dict) else item
            pending.extend((child, depth) for child in children)
    return maximum


class HistoryModel(ScriptedModel):
    def __init__(
        self, *, query_kind: str | None = None, deep_state: bool = False
    ) -> None:
        super().__init__()
        self.query_kind, self.deep_state = query_kind, deep_state

    def wake(self, bundle: Any) -> JsonObject:
        self.calls.append("wake")
        self.bundles.append(bundle)
        return {
            "action": "none",
            "note": "The retained diagnostic is within the callback limit.",
            "diagnostic": nested(126),
        }

    def respond(self, bundle: Any) -> JsonObject:
        self.calls.append("respond")
        self.bundles.append(bundle)
        if self.query_kind is not None and not bundle.get("history_pages"):
            return {
                "history_query": {
                    "after": 0,
                    "limit": 1,
                    "kind": self.query_kind,
                    "query": "",
                }
            }
        return {
            "state": nested(126) if self.deep_state else {"count": 1},
            "scope": "ongoing",
            "message": {
                "kind": "answer",
                "payload": {
                    "request": bundle["input"]["id"],
                    "text": "Recorded answer.",
                },
            },
        }


class HistoryDepthFixture(HybridFixture):
    def history_record(self) -> JsonObject:
        """Complete a legal wake, query its record, and retain the next model input."""
        self.model = HistoryModel(query_kind="control_completed")
        agent = self.start(
            provider=self.model,
            initial_route="llm",
            control_actors={"operator": ["wake"]},
        )
        result = agent.control(
            "operator",
            {
                "id": "boundary-wake",
                "action": "wake",
                "reason": "Inspect prior evidence",
            },
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(container_depth(result["reply"]), 128)
        self.assertEqual(decode(canonical(result["reply"])), result["reply"])
        completed = agent.receive(self.question("Inspect the completed wake"))
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.model.calls, ["wake", "respond", "respond"])
        frames = [
            frame
            for frame in self.store.records()
            if frame["kind"] == "model_input"
            and frame["data"]["request"]["bundle"].get("history_pages")
        ]
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        history = frame["data"]["request"]["bundle"]["history_pages"]
        self.assertEqual(len(history), 1)
        self.assertEqual(len(history[0]["records"]), 1)
        projected = history[0]["records"][0]
        self.assertEqual(projected["kind"], "control_completed")
        self.assertEqual(projected["data"]["result"]["reply"], result["reply"])
        self.assertEqual(container_depth(frame), 138)
        return frame

    def retained_record(self) -> JsonObject:
        """Retain real completed turns with an accepted boundary-depth state."""
        self.model = HistoryModel(deep_state=True)
        agent = self.start(provider=self.model, initial_route="llm")
        for number in (1, 2):
            result = agent.receive(self.question(f"Deep state turn {number}"))
            self.assertEqual(result["status"], "completed")
        self.assertEqual(self.model.calls, ["respond", "respond"])
        self.assertEqual(self.store.get("state"), nested(126))
        retained = agent.retain_history(target_bytes=1, minimum_steps=1)
        self.assertTrue(retained["pruned"])
        anchor = require_value(self.store.anchor(), "Expected the real retained anchor")
        self.assertEqual(anchor["snapshot"]["state"], nested(126))
        frames = [
            frame for frame in self.store.records() if frame["kind"] == "model_input"
        ]
        self.assertEqual(len(frames), 1)
        return frames[0]

    def recorded_result(self, frame: JsonObject) -> JsonObject:
        value = self.store.get("model:" + frame["data"]["id"])
        return value["execution"]["value"]
