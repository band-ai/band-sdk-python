"""Payload-free operational projection shared by HTTP and privileged inspection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from remote_agent.modeljournal import usage_summary
from remote_agent.types import JsonObject

if TYPE_CHECKING:
    from remote_agent.store import Store


EVENTS = (
    "fault",
    "llm_rejected",
    "turn_held",
    "publish_attempt",
    "publish_unknown",
    "room_rejected",
    "repair_held",
    "repair_interrupted",
    "repair_rejected",
    "program_installed",
)


def agent_summary(store: Store) -> JsonObject:
    # Read one committed state. The controller can run a bounded callback while
    # this short database lock is held; its conversation lock is not acquired.
    with store.lock:
        staged, repair, execution = (
            store.get(key) for key in ("staged", "repair", "execution")
        )
        pending = None
        if execution:
            producer = execution["producer"]
            key = (
                "invocation:" + execution["id"]
                if producer == "program"
                else "model:" + execution["id"] + ":respond"
            )
            recorded = store.get(key)
            status = recorded["status"] if recorded else "reserved"
            if recorded and recorded.get("execution", {}).get("uncertain"):
                status = "uncertain"
            pending = {"producer": producer, "status": status}
        # Count only fixed event categories. No identifiers, failure strings,
        # prompts, paths, candidate sources or program state enter this result.
        counts = dict.fromkeys(EVENTS, 0)
        placeholders = ",".join("?" for _ in EVENTS)
        counts.update(
            store.connection.execute(
                "SELECT kind,COUNT(*) FROM journal WHERE kind IN ("
                + placeholders
                + ") GROUP BY kind",
                EVENTS,
            ).fetchall()
        )
        return {
            "mode": store.get("mode"),
            "turns": store.get("turns", 0),
            "pending": len(store.get("queue", [])),
            "staged": staged is not None,
            "send_attempts": staged["sends"] if staged else 0,
            "execution": pending,
            "model_calls": store.get("model_calls", 0),
            "repair_calls": store.get("repair_calls", 0),
            "quarantined": store.get("quarantined", False),
            "repair_status": repair["status"] if repair else None,
            "events": counts,
            "model_usage": usage_summary(store),
        }
