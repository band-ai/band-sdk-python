"""Host-granted idempotent effects; the pure worker never performs external I/O."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from remote_agent.storage import digest
from remote_agent.types import JsonObject

if TYPE_CHECKING:
    from remote_agent.store import Store


class EffectRunner:
    def __init__(self, store: Store) -> None:
        self.store = store

    def run(
        self,
        identifier: str,
        request: JsonObject,
        dispatch: Callable[[JsonObject], JsonObject],
        validate: Callable[[JsonObject, JsonObject], bool],
    ) -> JsonObject:
        key = "effect:" + identifier
        fingerprint = digest(request)
        with self.store.lock:
            prior = self.store.get(key)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("effect_identity_conflict")
                if prior["status"] == "completed":
                    return prior["result"]
            else:
                pending = self.store.get("external_pending", [])
                if pending:
                    raise ValueError("reconcile_pending_external_effect")
                self.store.commit(
                    "external_input",
                    {"id": identifier, "request": request},
                    updates={
                        key: {
                            "fingerprint": fingerprint,
                            "status": "pending",
                            "request": request,
                        },
                        "external_pending": [identifier],
                    },
                )
        # The configured destination must deduplicate this exact stable request.
        # A lost acknowledgement retains it; a later explicit retry reconciles it.
        result = dispatch(request)
        if not validate(result, request):
            raise ValueError("external_acknowledgement_unknown")
        with self.store.lock:
            self.store.commit(
                "external_result",
                {"id": identifier, "result": result},
                updates={
                    key: {
                        "fingerprint": fingerprint,
                        "status": "completed",
                        "request": request,
                        "result": result,
                    },
                    "external_pending": [
                        i
                        for i in self.store.get("external_pending", [])
                        if i != identifier
                    ],
                },
            )
        return result
