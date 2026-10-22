"""Atomic prefix retention at completed agent turns; never an authority operation."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from remote_agent.storage import StoreError, canonical, digest
from remote_agent.types import JsonObject

if TYPE_CHECKING:
    from remote_agent.store import Store


PROFILE = "remote-agent/retention-anchor/1"
CACHE_PREFIXES = (
    "input:",
    "attempts:",
    "invocation:turn:",
    "model:turn:",
    "context:turn:",
    "effect:",
)


def anchor(store: Store) -> JsonObject | None:
    if not store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='retention_anchor'"
    ).fetchone():
        return None
    row = store.connection.execute(
        "SELECT data,hash FROM retention_anchor WHERE singleton=1"
    ).fetchone()
    if row is None:
        return None
    try:
        return validate_anchor(json.loads(row[0]), row[1])
    except (TypeError, ValueError, KeyError) as error:
        raise StoreError("Invalid retention anchor") from error


def validate_anchor(value: JsonObject, checksum: str) -> JsonObject:
    try:
        if (
            set(value)
            != {
                "profile",
                "sequence",
                "head",
                "state",
                "initialization",
                "snapshot",
                "input_floor",
            }
            or value["profile"] != PROFILE
            or digest(value) != checksum
            or not isinstance(value["head"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["head"])
            or type(value["sequence"]) is not int
            or value["sequence"] < 1
            or not isinstance(value["input_floor"], dict)
            or set(value["input_floor"]) != {"at", "ids"}
            or type(value["input_floor"]["at"]) is not int
            or not isinstance(value["input_floor"]["ids"], list)
            or any(
                not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v)
                for v in value["input_floor"]["ids"]
            )
            or not isinstance(value["state"], dict)
            or not isinstance(value["initialization"], dict)
            or set(value["snapshot"])
            != {"program", "schema", "state", "sequence", "head"}
            or value["snapshot"]["sequence"] != value["sequence"]
            or value["snapshot"]["head"] != value["head"]
            or value["snapshot"]["state"] != value["state"].get("state")
        ):
            raise StoreError("Corrupt retention anchor")
        return value
    except (TypeError, ValueError, KeyError) as error:
        raise StoreError("Invalid retention anchor") from error


def logical_bytes(store: Store) -> int:
    # SQLite length(TEXT) counts code points; retention budgets encoded bytes.
    anchor_bytes = 0
    if store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='retention_anchor'"
    ).fetchone():
        anchor_bytes = store.connection.execute(
            "SELECT COALESCE(SUM(length(CAST(data AS BLOB))+64),0) FROM retention_anchor"
        ).fetchone()[0]
    return anchor_bytes + sum(
        store.connection.execute(sql).fetchone()[0]
        for sql in (
            "SELECT COALESCE(SUM(length(CAST(data AS BLOB))+length(CAST(changes AS BLOB))+length(CAST(kind AS BLOB))+144),0) FROM journal",
            "SELECT COALESCE(SUM(length(CAST(data AS BLOB))+144),0) FROM snapshots",
            "SELECT COALESCE(SUM(length(CAST(key AS BLOB))+length(CAST(value AS BLOB))),0) FROM current_state",
        )
    )


def retain(
    store: Store, *, target_bytes: int = 100_000_000, minimum_steps: int = 100
) -> JsonObject:
    if (
        type(target_bytes) is not int
        or target_bytes < 1
        or type(minimum_steps) is not int
        or minimum_steps < 1
    ):
        raise StoreError("Invalid retention limits")
    with store.transaction():
        if (
            store.get("queue")
            or store.get("staged")
            or store.get("execution")
            or store.get("external_pending")
            or (store.get("repair") or {}).get("status")
            in ("requested", "running", "waiting", "interrupted")
        ):
            raise StoreError("Pending work prevents journal retention")
        total = logical_bytes(store)
        steps = store.connection.execute(
            "SELECT sequence FROM journal WHERE kind='turn' ORDER BY sequence DESC LIMIT ?",
            (minimum_steps + 1,),
        ).fetchall()
        result = {
            "pruned": False,
            "retained_steps": store.connection.execute(
                "SELECT COUNT(*) FROM journal WHERE kind='turn'"
            ).fetchone()[0],
            "logical_bytes": total,
            "target_bytes": target_bytes,
            "minimum_steps": minimum_steps,
            "over_target": total > target_bytes,
        }
        if total <= target_bytes or len(steps) <= minimum_steps:
            return result
        # A size-triggered sweep retains the configured complete-step floor.
        # Large mandatory steps and pinned operational state may exceed the soft target.
        cut = steps[-1][0]
        prior = anchor(store)
        state = dict(prior["state"]) if prior else {}
        initialization = prior["initialization"] if prior else None
        input_floor: JsonObject = (
            dict(prior["input_floor"]) if prior else {"at": -1, "ids": []}
        )
        last = None
        for row in store.connection.execute(
            "SELECT * FROM journal WHERE sequence<=? ORDER BY sequence", (cut,)
        ):
            frame = store._decode(row)
            state.update(frame["updates"])
            last = frame
            if frame["kind"] == "initialize":
                if initialization is not None:
                    raise StoreError("Ambiguous retained initialization")
                initialization = frame["data"]
            if frame["kind"] == "turn":
                at = frame["data"].get("input_at")
                if type(at) is not int or at < input_floor["at"]:
                    raise StoreError(
                        "Retention requires ordered authenticated input times"
                    )
                if at > input_floor["at"]:
                    input_floor = {"at": at, "ids": []}
                input_floor["ids"].append(digest(frame["data"]["input"]["id"]))
        if not last or not initialization or not isinstance(state.get("state"), dict):
            raise StoreError("Retention requires initialized agent state")
        protected = set()
        for row in store.connection.execute(
            "SELECT changes FROM journal WHERE sequence>?", (cut,)
        ):
            protected.update(json.loads(row[0]))
        removed = {
            key
            for key in state
            if key.startswith(CACHE_PREFIXES) and key not in protected
        }
        for key in removed:
            state.pop(key)
        snapshot = {
            "program": state["program"]["sha256"],
            "schema": state["metadata"]["schema"],
            "state": state["state"],
            "sequence": cut,
            "head": last["hash"],
        }
        value = {
            "profile": PROFILE,
            "sequence": cut,
            "head": last["hash"],
            "state": state,
            "initialization": initialization,
            "snapshot": snapshot,
            "input_floor": input_floor,
        }
        store.connection.execute(
            "CREATE TABLE IF NOT EXISTS retention_anchor (singleton INTEGER PRIMARY KEY CHECK(singleton=1),data TEXT NOT NULL,hash TEXT NOT NULL)"
        )
        store.connection.execute(
            "INSERT OR REPLACE INTO retention_anchor VALUES(1,?,?)",
            (canonical(value).decode(), digest(value)),
        )
        store.connection.execute("DELETE FROM snapshots WHERE sequence<=?", (cut,))
        store.connection.execute("DELETE FROM journal WHERE sequence<=?", (cut,))
        store.connection.executemany(
            "DELETE FROM current_state WHERE key=?", ((key,) for key in removed)
        )
        store._verify()
        store._verify_snapshots()
        size = logical_bytes(store)
        result.update(
            pruned=True,
            retained_steps=minimum_steps,
            logical_bytes=size,
            over_target=size > target_bytes,
            earliest_sequence=cut + 1,
            anchor_sequence=cut,
            input_floor=input_floor,
        )
        return result
